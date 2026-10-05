"""HTTP + WebSocket on one port.

One WebSocket connection is one session: the component body runs once per
connection, so its signals are per-client by construction.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, cast

from websockets.asyncio.server import ServerConnection, serve

from .forms import PayloadError, form_edits
from .reactive import Effect, batch
from .render import Fragment, Watcher, render

if TYPE_CHECKING:
    from collections.abc import Callable

    from websockets.http11 import Request, Response

    from .wire import InitMessage, Op, PatchMessage

STATIC = Path(__file__).parent / "static"


class Session:
    """Holds one client's signal graph and the ops it has produced."""

    def __init__(self, app_fn: Callable[[], Fragment]) -> None:
        self.pending: list[Op] = []
        self._live = False
        self.rendered = render(app_fn)
        # The first run of each effect registers its subscription; watchers were
        # seeded with the rendered value, so it produces no ops.
        self.effects = [self._watch(w) for w in self.rendered.watchers]
        self._live = True

    def _watch(self, watcher: Watcher) -> Effect:
        return Effect(lambda: self._collect(watcher))

    def _collect(self, watcher: Watcher) -> None:
        ops = watcher.refresh()

        if self._live and ops:
            self.pending.extend(ops)

    def dispatch(
        self,
        handler_id: str,
        value: object,
        revision: int | None = None,
        *,
        after: str | None = None,
        edits: list[tuple[str, object, int | None]] | None = None,
    ) -> list[Op]:
        fn = self.rendered.handlers.get(handler_id)

        if fn is None and not edits:
            return []
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
                fn(value)

            if after is not None and after != handler_id:
                callback = self.rendered.handlers.get(after)

                if callback is not None:
                    callback(value)
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

    def dispose(self) -> None:
        for eff in self.effects:
            eff.dispose()
        self.rendered.handlers.clear()
        self.rendered.bindings.clear()
        self.rendered.bind_elements.clear()
        self.rendered.handler_owners.clear()
        self.pending.clear()


def _static(connection: ServerConnection, name: str, content_type: str) -> Response:
    response = connection.respond(HTTPStatus.OK, (STATIC / name).read_text("utf-8"))
    # respond() hardcodes text/plain, and assigning a header appends rather
    # than replaces, so the original must be removed first.
    del response.headers["Content-Type"]
    response.headers["Content-Type"] = content_type

    return response


def _load_app(spec: str) -> Callable[[], Fragment]:
    module_name, _, attr = spec.partition(":")
    app: object = getattr(importlib.import_module(module_name), attr or "app")

    if not callable(app):
        raise TypeError("app must be callable")

    return cast("Callable[[], Fragment]", app)


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

        try:
            initial: InitMessage = {
                "t": "init",
                "html": session.rendered.body,
                "css": session.rendered.css,
            }
            await connection.send(json.dumps(initial))

            async for raw in connection:
                try:
                    decoded: object = json.loads(raw)
                except TypeError, ValueError:
                    continue

                if not isinstance(decoded, dict):
                    continue
                message = cast("dict[str, object]", decoded)

                if message.get("t") != "event":
                    continue
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
                    edits = form_edits(message.get("edits"))
                    ops = session.dispatch(
                        handler_id,
                        message.get("v"),
                        revision,
                        after=after,
                        edits=edits,
                    )
                except PayloadError:
                    continue

                if ops:
                    patch: PatchMessage = {"t": "patch", "ops": ops}
                    await connection.send(json.dumps(patch))
        finally:
            session.dispose()

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
