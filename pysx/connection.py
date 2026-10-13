"""Host-independent JSON protocol pump and ordered session outbox."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import parse_qs, urlsplit

from .forms import PayloadError, form_edits
from .http import HTTPRuntime, reconcile
from .request import browser_id
from .routing import Location
from .session import Session, close_session

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from .request import RequestContext
    from .wire import InitMessage, JSONValue, PatchMessage


class Connection(Protocol):
    request: RequestContext

    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


async def serve_connection(runtime: HTTPRuntime, connection: Connection) -> None:
    request = connection.request
    supplied = parse_qs(urlsplit(request.path).query).get("location", [request.base_path or "/"])

    try:
        Location.parse(supplied[0])
    except ValueError:
        await connection.close(code=1008, reason="invalid initial location")

        return
    adoption = parse_qs(urlsplit(request.path).query, keep_blank_values=True).get("adoption", [])
    adopted = bool(adoption)

    if adopted:
        try:
            session = runtime.claim(adoption[0], request, browser_id(request))
        except ValueError:
            await connection.close(code=1008, reason="invalid adoption")

            return

        try:
            hello: object = json.loads(await asyncio.wait_for(connection.recv(), 10))

            if not isinstance(hello, dict):
                raise PayloadError("expected adoption edits")
            message = cast("dict[str, object]", hello)

            if message.get("t") != "adopt":
                raise PayloadError("expected adoption edits")
            async with session.runtime.turn():
                reconcile(session, form_edits(message.get("edits")))
                session.routes.observe(supplied[0])
        except Exception:
            session.dispose()
            await connection.close(code=1008, reason="invalid adoption edits")

            return
        except asyncio.CancelledError:
            session.dispose()

            raise
    else:
        session = Session(
            runtime.app,
            location=supplied[0],
            development=runtime.development,
            request=request,
            mutation_capacity=runtime.mutation_capacity,
            task_capacity=runtime.task_capacity,
        )
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=64)
    announced_mounts: set[str] = set()

    async def send_mounts() -> None:
        pending = session.rendered.scopes.pending_mounts()
        announced_mounts.intersection_update(pending)
        tokens = [token for token in pending if token not in announced_mounts]

        for start in range(0, len(tokens), 1024):
            await connection.send(json.dumps({"t": "mount", "ids": tokens[start : start + 1024]}))
            announced_mounts.update(tokens[start : start + 1024])

    async def send_navigation() -> None:
        commands, session.routes.commands = session.routes.commands, []

        for command in commands:
            await connection.send(json.dumps(command))

    async def send_patch() -> None:
        if session.pending:
            await connection.send(json.dumps({"t": "patch", "ops": session.pending}))
            session.pending = []

    async def send_command(message: dict[str, JSONValue]) -> None:
        await send_patch()
        await send_navigation()
        await connection.send(json.dumps(message))

    session.rendered.dom.sender = send_command

    async def handle_message(message: dict[str, object]) -> bool:
        handler_id = message.get("h", "")
        revision = message.get("rev")
        after = message.get("after")

        if (
            not isinstance(handler_id, str)
            or (revision is not None and (type(revision) is not int or revision < 0))
            or (after is not None and not isinstance(after, str))
        ):
            await connection.send(
                json.dumps({"t": "result", "h": "", "status": "invalid", "ops": []})
            )

            return True

        try:
            if message.get("t") == "location":
                url = message.get("url")

                if not isinstance(url, str) or not session.routes.accept_revision(
                    message.get("rev")
                ):
                    return True

                try:
                    session.routes.observe(url)
                except ValueError as error:
                    raise PayloadError("invalid location observation") from error
                session.routes.commands.append(
                    {
                        "t": "navigation",
                        "url": session.routes.location.url,
                        "rev": session.routes.revision,
                        "mode": "observe",
                    }
                )
                ops, session.pending = session.pending, []
            elif message.get("t") == "mounted":
                tokens = message.get("ids")

                if isinstance(tokens, list):
                    supplied = cast("list[object]", tokens)

                    if len(supplied) <= 1024 and all(isinstance(token, str) for token in supplied):
                        session.rendered.scopes.acknowledge(cast("list[str]", supplied))
                ops, session.pending = session.pending, []
            else:
                nav_revision = message.get("nav_rev")

                if nav_revision is not None and not session.routes.accept_revision(nav_revision):
                    return True
                outcome = await session.dispatch_result_async(
                    handler_id,
                    message.get("v"),
                    revision,
                    after=after,
                    edits=form_edits(message.get("edits")),
                    event=message.get("event"),
                    navigation=message.get("navigation") is True,
                )

                if outcome["status"] != "success":
                    await connection.send(json.dumps(outcome))
                    await send_navigation()
                    await send_mounts()

                    if session.rendered.unrepaired:
                        await connection.close(
                            code=1011, reason="render recovery requires a boundary"
                        )

                        return False

                    return True
                ops = outcome["ops"]
        except PayloadError:
            repair, session.pending = session.pending, []
            await connection.send(
                json.dumps({"t": "result", "h": handler_id, "status": "invalid", "ops": repair})
            )
            await send_navigation()
            await send_mounts()

            return True
        except Exception:
            await connection.close(code=1011, reason="event handler failed")

            return False

        if session.rendered.failures:
            info = session.rendered.failures[0]
            session.rendered.failures.clear()
            await connection.send(
                json.dumps(
                    {
                        "t": "result",
                        "h": handler_id,
                        "status": "error",
                        "ops": ops,
                        "error": info.wire(),
                    }
                )
            )
        elif ops:
            patch: PatchMessage = {"t": "patch", "ops": ops}
            await connection.send(json.dumps(patch))
        await send_navigation()
        await send_mounts()

        return True

    async def event_worker() -> None:
        while True:
            message = await queue.get()
            async with session.runtime.turn():
                if not await handle_message(message):
                    return

    worker: asyncio.Task[None] | None = None
    outbox: asyncio.Task[None] | None = None

    async def push_worker() -> None:
        while True:
            await session.changed.wait()
            async with session.runtime.lock:
                session.changed.clear()

                while session.rendered.failures:
                    info = session.rendered.failures.pop(0)
                    ops, session.pending = session.pending, []
                    await connection.send(
                        json.dumps(
                            {
                                "t": "result",
                                "h": "",
                                "status": "error",
                                "ops": ops,
                                "error": info.wire(),
                            }
                        )
                    )

                    if session.rendered.unrepaired:
                        await connection.close(
                            code=1011, reason="render recovery requires a boundary"
                        )

                        return
                await send_patch()
                await send_navigation()
                await send_mounts()

    try:
        initial: InitMessage = {
            "t": "init",
            "html": session.rendered.body,
            "css": session.rendered.css,
            "routing": bool(session.routes.routers),
        }
        await connection.send(
            json.dumps(
                {"t": "adopted", "routing": bool(session.routes.routers)} if adopted else initial
            )
        )
        await send_patch()
        await send_navigation()
        await send_mounts()
        worker = asyncio.create_task(event_worker())
        outbox = asyncio.create_task(push_worker())

        async for raw in connection:
            try:
                decoded: object = json.loads(raw)
            except TypeError, ValueError:
                await connection.send(
                    json.dumps({"t": "result", "h": "", "status": "invalid", "ops": []})
                )

                continue

            if not isinstance(decoded, dict):
                await connection.send(
                    json.dumps({"t": "result", "h": "", "status": "invalid", "ops": []})
                )

                continue
            message = cast("dict[str, object]", decoded)

            if message.get("t") == "dom_reply":
                session.rendered.dom.reply(message)
            elif message.get("t") in ("event", "mounted", "location"):
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    await connection.close(code=1008, reason="event queue limit")
    except BaseException as error:
        if outbox is not None:
            outbox.cancel()
            await asyncio.gather(outbox, return_exceptions=True)
        await close_session(session, worker, error)

        raise

    outbox.cancel()
    await asyncio.gather(outbox, return_exceptions=True)
    await close_session(session, worker, None)
