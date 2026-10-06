"""HTTP + WebSocket on one port.

One WebSocket connection is one session: the component body runs once per
connection, so its signals are per-client by construction.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, cast

from websockets.asyncio.server import ServerConnection, serve

from .dom import DomError
from .events import decode_event
from .forms import PayloadError, form_edits
from .reactive import Effect, batch
from .render import Fragment, Watcher, render
from .styles import style_context

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from string.templatelib import Template

    from websockets.http11 import Request, Response

    from .wire import InitMessage, JSONValue, Op, PatchMessage

STATIC = Path(__file__).parent / "static"


class Session:
    """Holds one client's signal graph and the ops it has produced."""

    def __init__(self, app_fn: Callable[[], Template | Fragment]) -> None:
        self.pending: list[Op] = []
        self._awaitables: list[Awaitable[object]] = []
        self._live = False
        self.rendered = render(app_fn)
        # The first run of each effect registers its subscription; watchers were
        # seeded with the rendered value, so it produces ops only where setup
        # wrote a signal after its hole had already been emitted.
        self.effects: list[Effect] = []

        try:
            for watcher in self.rendered.watchers:
                self.effects.append(self._watch(watcher))
        except BaseException as error:
            try:
                self.dispose()
            except Exception as cleanup_error:
                error.add_note(f"session cleanup also failed: {cleanup_error}")

            raise
        self._live = True

    def _watch(self, watcher: Watcher) -> Effect:
        return Effect(lambda: self._collect(watcher))

    def _collect(self, watcher: Watcher) -> None:
        token = style_context.set(self.rendered.styles)

        try:
            ops = watcher.refresh()
        finally:
            style_context.reset(token)
        self._sync_styles()

        if ops:
            self.pending.extend(ops)

    def _sync_styles(self) -> None:
        css = self.rendered.styles.snapshot()

        if css != self.rendered.css:
            self.rendered.css = css

            if self._live:
                self.pending[:] = [op for op in self.pending if op["op"] != "css"]
                self.pending.insert(0, {"op": "css", "v": css})

    def _invoke(self, callback: Callable[[object], object], value: object) -> None:
        token = style_context.set(self.rendered.styles)

        try:
            result = callback(value)
        finally:
            style_context.reset(token)
        self._sync_styles()

        if inspect.isawaitable(result):
            self._awaitables.append(result)

    async def dispatch_async(
        self,
        handler_id: str,
        value: object,
        revision: int | None = None,
        *,
        after: str | None = None,
        edits: list[tuple[str, object, int | None]] | None = None,
        event: object = None,
    ) -> list[Op]:
        ops = self.dispatch(handler_id, value, revision, after=after, edits=edits, event=event)
        self.pending.extend(ops)

        style_token = style_context.set(self.rendered.styles)

        try:
            while self._awaitables:
                await self._awaitables.pop(0)
                self._sync_styles()
        except BaseException:
            self.cancel_callbacks()

            raise
        finally:
            style_context.reset(style_token)
        ops = self.pending
        self.pending = []

        return ops

    def dispatch(
        self,
        handler_id: str,
        value: object,
        revision: int | None = None,
        *,
        after: str | None = None,
        edits: list[tuple[str, object, int | None]] | None = None,
        event: object = None,
    ) -> list[Op]:
        fn = self.rendered.handlers.get(handler_id)
        listener = self.rendered.dom.listeners.get(handler_id)

        if listener is not None:
            fn = cast("Callable[[object], object]", listener[0].callback)

        if fn is None and not edits:
            return []
        typed = self.rendered.event_handlers.get(handler_id)

        if listener is not None:
            typed = (listener[0], listener[1])
        following = self.rendered.event_handlers.get(after or "")
        snapshot = decode_event(event, handler_id, typed[1]) if typed else None
        after_snapshot = decode_event(event, after or "", following[1]) if following else None
        updates = list(edits or [])
        binding = self.rendered.bindings.get(handler_id)

        if binding is not None:
            updates.append((handler_id, value, revision))

        for hid, payload, _rev in updates:
            target = self.rendered.bindings.get(hid)

            if target is not None:
                target.validate(payload)
        self.pending.clear()
        with batch():
            for hid, payload, _rev in edits or []:
                target = self.rendered.bindings.get(hid)

                if target is not None:
                    target.set(payload)

            if fn is not None:
                if typed is not None and snapshot is not None:
                    self._invoke(cast("Callable[[object], object]", typed[0].callback), snapshot)
                else:
                    self._invoke(fn, value)

            if after is not None and after != handler_id:
                callback = self.rendered.handlers.get(after)

                if callback is not None:
                    if following is not None and after_snapshot is not None:
                        self._invoke(
                            cast("Callable[[object], object]", following[0].callback),
                            after_snapshot,
                        )
                    else:
                        self._invoke(callback, value)
        ops = self.pending
        self.pending = []

        for hid, payload, rev in updates:
            target = self.rendered.bindings.get(hid)

            if target is None:
                continue
            equal = target.signal() == payload
            own = [
                op
                for op in ops
                if (op["op"] == "attr" or op["op"] == "prop")
                and op["id"] == target.element
                and op["name"] == target.name
            ]

            if equal:
                ops = [op for op in ops if op not in own]
            else:
                correction = target.op()

                if rev is not None and (correction["op"] == "attr" or correction["op"] == "prop"):
                    correction["rev"] = rev
                ops = [op for op in ops if op not in own]
                ops.append(correction)

        return ops

    def cancel_callbacks(self) -> None:
        for awaitable in self._awaitables:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
        self._awaitables.clear()

    def dispose(self) -> None:
        self.cancel_callbacks()
        errors: list[Exception] = []

        for eff in self.effects:
            try:
                eff.dispose()
            except Exception as error:
                errors.append(error)
        self.effects.clear()
        self.pending.clear()

        try:
            self.rendered.dispose()
        except Exception as error:
            errors.append(error)

        if errors:
            raise ExceptionGroup("session cleanup failed", errors)


async def close_session(
    session: Session, worker: asyncio.Task[None] | None, error: BaseException | None
) -> None:
    """Dispose a connection's session without masking the error that ended it."""
    if worker is not None:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    try:
        session.dispose()
    except Exception as cleanup_error:
        if error is None:
            raise
        error.add_note(f"session cleanup also failed: {cleanup_error}")


def _static(connection: ServerConnection, name: str, content_type: str) -> Response:
    response = connection.respond(HTTPStatus.OK, (STATIC / name).read_text("utf-8"))
    # respond() hardcodes text/plain, and assigning a header appends rather
    # than replaces, so the original must be removed first.
    del response.headers["Content-Type"]
    response.headers["Content-Type"] = content_type

    return response


def _load_app(spec: str) -> Callable[[], Template | Fragment]:
    module_name, _, attr = spec.partition(":")
    app: object = getattr(importlib.import_module(module_name), attr or "app")

    if not callable(app):
        raise TypeError("app must be callable")

    return cast("Callable[[], Template | Fragment]", app)


def main() -> None:
    ap = argparse.ArgumentParser(prog="pysx.server")
    ap.add_argument("--app", required=True, help="module:attr of the @component to serve")
    ap.add_argument("--port", type=int, default=8750)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    app_fn = _load_app(args.app)

    async def process_request(connection: ServerConnection, request: Request) -> Response | None:
        if request.path in ("/", "/index.html"):
            return _static(connection, "index.html", "text/html; charset=utf-8")

        if request.path == "/client.js":
            return _static(connection, "client.js", "text/javascript; charset=utf-8")

        if request.path == "/ws":
            return None

        return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")

    async def handler(connection: ServerConnection) -> None:
        session = Session(app_fn)
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=64)

        async def send_mounts() -> None:
            tokens = session.rendered.scopes.pending_mounts()

            for start in range(0, len(tokens), 1024):
                await connection.send(
                    json.dumps({"t": "mount", "ids": tokens[start : start + 1024]})
                )

        async def send_patch() -> None:
            if session.pending:
                await connection.send(json.dumps({"t": "patch", "ops": session.pending}))
                session.pending = []

        async def send_command(message: dict[str, JSONValue]) -> None:
            await send_patch()
            await connection.send(json.dumps(message))

        session.rendered.dom.sender = send_command

        async def event_worker() -> None:
            while True:
                message = await queue.get()
                handler_id = message.get("h", "")

                if not isinstance(handler_id, str):
                    continue
                revision = message.get("rev")
                after = message.get("after")

                if revision is not None and (type(revision) is not int or revision < 0):
                    continue

                if after is not None and not isinstance(after, str):
                    continue

                try:
                    if message.get("t") == "mounted":
                        tokens = message.get("ids")

                        if isinstance(tokens, list):
                            supplied = cast("list[object]", tokens)

                            if len(supplied) <= 1024 and all(
                                isinstance(token, str) for token in supplied
                            ):
                                session.rendered.scopes.acknowledge(cast("list[str]", supplied))
                        ops = session.pending
                        session.pending = []
                    else:
                        ops = await session.dispatch_async(
                            handler_id,
                            message.get("v"),
                            revision,
                            after=after,
                            edits=form_edits(message.get("edits")),
                            event=message.get("event"),
                        )
                except PayloadError, DomError:
                    await send_patch()
                    await send_mounts()

                    continue
                except Exception:
                    await connection.close(code=1011, reason="event handler failed")

                    return

                if ops:
                    patch: PatchMessage = {"t": "patch", "ops": ops}
                    await connection.send(json.dumps(patch))
                await send_mounts()

        worker: asyncio.Task[None] | None = None

        try:
            initial: InitMessage = {
                "t": "init",
                "html": session.rendered.body,
                "css": session.rendered.css,
            }
            await connection.send(json.dumps(initial))
            await send_patch()
            await send_mounts()
            worker = asyncio.create_task(event_worker())

            async for raw in connection:
                try:
                    decoded: object = json.loads(raw)
                except TypeError, ValueError:
                    continue

                if not isinstance(decoded, dict):
                    continue
                message = cast("dict[str, object]", decoded)

                if message.get("t") == "dom_reply":
                    session.rendered.dom.reply(message)
                elif message.get("t") in ("event", "mounted"):
                    try:
                        queue.put_nowait(message)
                    except asyncio.QueueFull:
                        await connection.close(code=1008, reason="event queue limit")
        except BaseException as error:
            await close_session(session, worker, error)

            raise
        await close_session(session, worker, None)

    async def run() -> None:
        async with serve(handler, args.host, args.port, process_request=process_request):
            print(f"pysx ready -> http://{args.host}:{args.port}", flush=True)
            await asyncio.get_running_loop().create_future()

    try:
        asyncio.run(run())
    except OSError as exc:
        raise SystemExit(f"cannot bind {args.host}:{args.port}: {exc}") from exc
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
