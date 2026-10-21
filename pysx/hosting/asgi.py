"""ASGI 3 HTTP, WebSocket and lifespan adapter for the shared application engine."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, quote, urlsplit

from pysx.runtime.connection import BoundedConnection

from .hosting import request_from_headers
from .security import AccessDenied

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from .hosting import Application
    from .request import RequestContext

type Event = dict[str, object]
type Receive = Callable[[], Awaitable[Event]]
type Send = Callable[[Event], Awaitable[None]]


class DisconnectedError(Exception):
    pass


class ASGI:
    def __init__(self, application: Application) -> None:
        self.application = application

    def _request(self, scope: Event) -> RequestContext:
        root = str(scope.get("root_path", ""))
        prefix = self.application.config.base_path or quote(root.rstrip("/"), safe="/")
        raw = scope.get("raw_path")
        path = (
            raw.decode("ascii")
            if isinstance(raw, bytes)
            else quote(str(scope.get("path", "/")), safe="/")
        )

        if root and prefix and path != prefix and not path.startswith(prefix + "/"):
            path = prefix + path
        query = scope.get("query_string", b"")

        if isinstance(query, bytes) and query:
            path += "?" + query.decode("ascii")
        headers = [
            (name.decode("latin-1"), value.decode("latin-1"))
            for name, value in cast("list[tuple[bytes, bytes]]", scope.get("headers", []))
        ]
        state = scope.get("state", {})
        principal = cast("dict[str, object]", state).get("pysx_principal")

        if principal is not None and not isinstance(principal, str):
            raise ValueError("middleware principal must be a string or None")

        client = scope.get("client")
        peer = (
            str(cast("tuple[object, ...]", client)[0])
            if isinstance(client, (tuple, list)) and client
            else None
        )
        scheme = "https" if scope.get("scheme") in ("https", "wss") else "http"

        return request_from_headers(
            path, headers, base_path=prefix, principal=principal, scheme=scheme, peer=peer
        )

    async def __call__(self, scope: Event, receive: Receive, send: Send) -> None:
        kind = scope.get("type")

        if kind == "lifespan":
            while True:
                event = await receive()

                if event.get("type") == "lifespan.startup":
                    try:
                        await self.application.start()
                    except Exception:
                        await send(
                            {
                                "type": "lifespan.startup.failed",
                                "message": "application startup failed",
                            }
                        )

                        return
                    await send({"type": "lifespan.startup.complete"})
                elif event.get("type") == "lifespan.shutdown":
                    try:
                        await self.application.close()
                    except Exception:
                        await send(
                            {
                                "type": "lifespan.shutdown.failed",
                                "message": "application shutdown failed",
                            }
                        )

                        return
                    await send({"type": "lifespan.shutdown.complete"})

                    return

            return

        try:
            request = self._request(scope)
        except ValueError, UnicodeError:
            if kind == "http":
                await send({"type": "http.response.start", "status": 403, "headers": []})
                await send({"type": "http.response.body", "body": b"access denied"})
            elif kind == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": "access denied"})

            return

        if kind == "http":
            event = await receive()

            if event.get("type") == "http.disconnect":
                return

            if event.get("type") != "http.request":
                raise ValueError("expected http.request")

            if event.get("body") or event.get("more_body"):
                await send({"type": "http.response.start", "status": 413, "headers": []})
                await send({"type": "http.response.body", "body": b"request body unsupported"})

                return
            result = await self.application.http(request, method=str(scope.get("method", "GET")))

            try:
                await send(
                    {
                        "type": "http.response.start",
                        "status": result.status,
                        "headers": [
                            (name.encode("ascii"), value.encode("latin-1"))
                            for name, value in result.headers
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": result.body})
            except BaseException:
                if result.adoption is not None:
                    self.application.runtime.expire(result.adoption)

                raise

            return

        if kind != "websocket":
            raise ValueError("unsupported ASGI scope")

        if (await receive()).get("type") != "websocket.connect":
            return

        try:
            context = self.application.prepare(request, websocket=True)
            assert context.access is not None
            context.access("websocket", "/ws")
        except AccessDenied:
            self.application.observed(request, "websocket", 1008)
            await send({"type": "websocket.close", "code": 1008, "reason": "access denied"})

            return

        connection = _Connection(context, receive, send, scope)
        bounded = BoundedConnection(connection, self.application.config.transport)

        if urlsplit(context.path).path != context.base_path + "/ws":
            await bounded.close(1008, "invalid socket path")

            return

        if not self.application.admit_handshake(id(connection), bounded):
            await bounded.close(1013, "connection capacity")

            return

        try:
            query = parse_qs(urlsplit(context.path).query)

            if (
                not query.get("adoption")
                and query.get("resume") != ["1"]
                and query.get("recover") != ["1"]
            ):
                if not self.application.runtime.reserve():
                    await bounded.close(1013, "graph capacity")

                    return
                connection.reserved = True
            async with asyncio.timeout(self.application.config.transport.hello_timeout):
                await send({"type": "websocket.accept"})
            await self.application.websocket(connection)
        except BaseException:
            connection.abort()

            raise
        finally:
            self.application.handshakes.pop(id(connection), None)

            if connection.reserved:
                connection.reserved = False
                self.application.runtime.unreserve()


class _Connection:
    def __init__(self, request: RequestContext, receive: Receive, send: Send, scope: Event) -> None:
        self.request = request
        self.receive = receive
        self.sender = send
        self.closed = asyncio.Event()
        self.reserved = False
        extensions = cast("dict[str, dict[str, object]]", scope.get("extensions", {}))
        abort = extensions.get("pysx.transport", {}).get("abort")
        self.abort_transport = cast("Callable[[], None] | None", abort) if callable(abort) else None
        versions = cast("dict[str, str]", scope.get("asgi", {}))
        self.reason = tuple(
            int(part) for part in versions.get("spec_version", "2.0").split(".")
        ) >= (2, 3)

    async def send(self, message: str) -> None:
        if self.closed.is_set():
            raise OSError("connection closed")
        await self.sender({"type": "websocket.send", "text": message})

    async def recv(self) -> str | bytes:
        if self.closed.is_set():
            raise DisconnectedError
        incoming = asyncio.ensure_future(self.receive())
        closing = asyncio.create_task(self.closed.wait())

        try:
            done, _pending = await asyncio.wait(
                (incoming, closing), return_when=asyncio.FIRST_COMPLETED
            )

            if closing in done:
                raise DisconnectedError
            event = await incoming

            if event.get("type") == "websocket.disconnect":
                self.close_code = cast("int", event.get("code", 1000))
                self.closed.set()

                raise DisconnectedError

            if event.get("type") != "websocket.receive":
                raise ValueError("expected websocket.receive")
            value = event.get("text")

            if isinstance(value, str):
                return value
            value = event.get("bytes")

            if isinstance(value, bytes):
                return value

            raise ValueError("expected a text or binary message")
        finally:
            for task in (incoming, closing):
                if not task.done():
                    task.cancel()
            await asyncio.gather(incoming, closing, return_exceptions=True)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        event: Event = {"type": "websocket.close", "code": code}

        if self.reason:
            event["reason"] = reason
        await self.sender(event)

    def abort(self) -> None:
        self.closed.set()
        abort = self.abort_transport

        if abort is not None:
            abort()

    async def __aiter__(self) -> AsyncIterator[str | bytes]:
        while True:
            try:
                yield await self.recv()
            except DisconnectedError:
                return
