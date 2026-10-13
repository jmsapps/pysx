"""Shared application configuration and HTTP/connection ownership for both hosts."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import mimetypes
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from http import HTTPStatus
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

from .connection import serve_connection
from .errors import ErrorInfo, report_error
from .http import HTTPResult, HTTPRuntime
from .request import RequestContext, browser_id
from .routing import Location

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping
    from string.templatelib import Template

    from websockets.asyncio.server import Server, ServerConnection
    from websockets.http11 import Request, Response

    from .asgi import ASGI
    from .connection import Connection
    from .render import Fragment

type Hook = Callable[[], Awaitable[None] | None]


@dataclass(frozen=True)
class StaticAsset:
    body: bytes
    content_type: str
    cache_seconds: int = 0

    @classmethod
    def from_path(cls, path: Path, *, content_type: str | None = None) -> StaticAsset:
        return cls(
            path.read_bytes(),
            content_type or mimetypes.guess_type(path)[0] or "application/octet-stream",
        )

    def response(self, headers: Mapping[str, str]) -> HTTPResult:
        etag = '"' + hashlib.sha256(self.body).hexdigest() + '"'
        metadata = (
            ("content-type", self.content_type),
            ("etag", etag),
            ("cache-control", f"public, max-age={self.cache_seconds}, must-revalidate"),
        )
        matching = headers.get("if-none-match", "").split(",")
        fresh = any(value.strip() in (etag, "*", "W/" + etag) for value in matching)

        return HTTPResult(304 if fresh else 200, metadata, b"" if fresh else self.body)


@dataclass(frozen=True)
class HostConfig:
    base_path: str = ""
    render_capacity: int = 128
    render_ttl: float = 30
    mutation_capacity: int = 64
    task_capacity: int = 1024
    development: bool = False
    secure_cookies: bool = False
    shutdown_timeout: float = 5
    principal: Callable[[RequestContext], str | None] | None = None
    assets: Mapping[str, StaticAsset] = field(default_factory=lambda: dict[str, StaticAsset]())
    startup: tuple[Hook, ...] = ()
    shutdown: tuple[Hook, ...] = ()

    def __post_init__(self) -> None:
        location = Location.parse(self.base_path or "/")

        if (
            location.search
            or location.hash
            or self.shutdown_timeout <= 0
            or self.mutation_capacity < 1
            or self.task_capacity < 1
        ):
            raise ValueError("invalid host prefix, shutdown timeout or capacity")
        object.__setattr__(
            self, "base_path", quote(location.path.rstrip("/"), safe="/%:@-._~!$&'()*+,;=")
        )
        object.__setattr__(self, "assets", MappingProxyType(dict(self.assets)))


def request_from_headers(
    path: str, headers: list[tuple[str, str]], *, base_path: str = "", principal: str | None = None
) -> RequestContext:
    combined: dict[str, str] = {}

    for name, value in headers:
        key = name.lower()
        separator = "; " if key == "cookie" else ", "
        combined[key] = combined[key] + separator + value if key in combined else value

    return RequestContext(
        path=path, headers=MappingProxyType(combined), principal=principal, base_path=base_path
    )


class Application:
    def __init__(
        self, app: Callable[[], Template | Fragment], *, config: HostConfig | None = None
    ) -> None:
        self.app = app
        self.config = config or HostConfig()
        self.runtime = HTTPRuntime(
            app,
            capacity=self.config.render_capacity,
            ttl=self.config.render_ttl,
            development=self.config.development,
            mutation_capacity=self.config.mutation_capacity,
            task_capacity=self.config.task_capacity,
        )
        self.assets = {
            "/client.js": StaticAsset(
                (Path(__file__).parent / "static/client.js").read_bytes(),
                "text/javascript; charset=utf-8",
            ),
            **self.config.assets,
        }
        self.closed = False
        self.started = False
        self.start_lock = asyncio.Lock()
        self.connections: dict[int, tuple[Connection, asyncio.Task[object]]] = {}

    def path(self, path: str) -> str:
        return self.config.base_path + "/" + path.lstrip("/")

    def prepare(self, request: RequestContext) -> RequestContext:
        context = replace(request, base_path=request.base_path or self.config.base_path)

        if context.principal is None and self.config.principal is not None:
            context = replace(context, principal=self.config.principal(context))

        return context

    async def start(self) -> None:
        async with self.start_lock:
            if self.closed:
                raise RuntimeError("application has been closed")

            if self.started:
                return

            for hook in self.config.startup:
                result = hook()

                if inspect.isawaitable(result):
                    await result
            self.started = True

    async def http(self, request: RequestContext, *, method: str = "GET") -> HTTPResult:
        if self.closed:
            return HTTPResult(503, (), b"application closed")

        if method not in ("GET", "HEAD"):
            return HTTPResult(405, (("allow", "GET, HEAD"),), b"method not allowed")
        await self.start()
        context = self.prepare(request)
        path = urlsplit(context.path).path
        prefix = context.base_path

        if prefix and path != prefix and not path.startswith(prefix + "/"):
            return HTTPResult(404, (), b"not found")
        local = path[len(prefix) :] or "/"
        asset = self.assets.get(local)

        if asset is not None:
            result = asset.response(context.headers)
        else:
            browser = browser_id(context) or secrets.token_urlsafe(32)

            try:
                result = self.runtime.render(context, browser, client_path=prefix + "/client.js")
            except Exception as error:
                report_error(ErrorInfo(None, "render"), error, development=self.config.development)

                return HTTPResult(
                    500, (("cache-control", "private, no-store"),), b"application render failed"
                )
            cookie = f"pysx_browser={browser}; Path=/; HttpOnly; SameSite=Lax" + (
                "; Secure" if self.config.secure_cookies else ""
            )
            result = replace(result, headers=(*result.headers, ("set-cookie", cookie)))

        if method == "HEAD":
            if result.adoption is not None:
                self.runtime.expire(result.adoption)

            return replace(
                result,
                headers=(*result.headers, ("content-length", str(len(result.body)))),
                body=b"",
                adoption=None,
            )

        return result

    async def websocket(self, connection: Connection) -> None:
        await self.start()

        if urlsplit(connection.request.path).path != connection.request.base_path + "/ws":
            await connection.close(code=1008, reason="invalid socket path")

            return
        task = asyncio.current_task()
        assert task is not None
        identifier = id(connection)
        self.connections[identifier] = (connection, task)

        try:
            await serve_connection(self.runtime, connection)
        finally:
            self.connections.pop(identifier, None)

    async def close(self) -> None:
        async with self.start_lock:
            if self.closed:
                return
            self.closed = True
        errors: list[Exception] = []
        sessions = [entry.session for entry in self.runtime.pending.values()]
        pending_tasks = {task for session in sessions for task in session.runtime.tasks}
        workers = {
            session.runtime.worker for session in sessions if session.runtime.worker is not None
        }

        try:
            self.runtime.close()
        except Exception as error:
            errors.append(error)
        active = tuple(self.connections.values())

        if active:
            outcomes = await asyncio.gather(
                *(connection.close() for connection, _task in active), return_exceptions=True
            )
            errors.extend(outcome for outcome in outcomes if isinstance(outcome, Exception))
        current = asyncio.current_task()
        tasks = (
            {task for _connection, task in active if task is not current} | pending_tasks | workers
        )

        for task in tasks:
            task.cancel()

        if tasks:
            done, _pending = await asyncio.wait(tasks, timeout=self.config.shutdown_timeout)

            for task in done:
                if not task.cancelled() and isinstance(
                    outcome_error := task.exception(), Exception
                ):
                    errors.append(outcome_error)

        for hook in reversed(self.config.shutdown):
            try:
                result = hook()

                if inspect.isawaitable(result):
                    await result
            except Exception as error:
                errors.append(error)

        if errors:
            raise ExceptionGroup("application shutdown failed", errors)

    def asgi(self) -> ASGI:
        from .asgi import ASGI

        return ASGI(self)

    @asynccontextmanager
    async def listen(self, host: str = "127.0.0.1", port: int = 8750) -> AsyncGenerator[Server]:
        from websockets.asyncio.server import ServerConnection, serve
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        await self.start()

        class HTTPConnection(ServerConnection):
            # The websocket handshake parser accepts only GET. Keep the HTTP
            # method separately before passing the request through that parser.
            method: str = "GET"
            first_line: bytes = b""
            reading_line: bool = True

            def data_received(self, data: bytes) -> None:
                if not self.reading_line:
                    super().data_received(data)

                    return
                self.first_line += data

                if len(self.first_line) > 8192 and b"\r\n" not in self.first_line:
                    self.transport.close()

                    return

                if b"\r\n" not in self.first_line:
                    return
                method, separator, rest = self.first_line.partition(b" ")
                self.method = method.decode("ascii", errors="replace")
                self.reading_line = False
                self.first_line = b""
                super().data_received(b"GET" + separator + rest)

        async def process_request(
            _connection: ServerConnection, request: Request
        ) -> Response | None:
            context = request_from_headers(
                request.path, list(request.headers.raw_items()), base_path=self.config.base_path
            )

            method = _connection.method if isinstance(_connection, HTTPConnection) else "GET"

            if method == "GET" and urlsplit(request.path).path == self.path("ws"):
                return None
            result = await self.http(context, method=method)

            try:
                reason = HTTPStatus(result.status).phrase
            except ValueError:
                reason = ""
            headers = Headers(result.headers)

            if "Content-Length" not in headers:
                headers["Content-Length"] = str(len(result.body))

            return Response(result.status, reason, headers, result.body)

        async def handler(connection: ServerConnection) -> None:
            request = connection.request
            assert request is not None
            context = self.prepare(
                request_from_headers(
                    request.path, list(request.headers.raw_items()), base_path=self.config.base_path
                )
            )
            await self.websocket(_StandaloneConnection(connection, context))

        try:
            async with serve(
                handler,
                host,
                port,
                process_request=process_request,
                create_connection=HTTPConnection,
                close_timeout=self.config.shutdown_timeout,
            ) as server:
                yield server
        finally:
            await self.close()


class _StandaloneConnection:
    def __init__(self, connection: ServerConnection, request: RequestContext) -> None:
        self.connection = connection
        self.request = request

    async def send(self, message: str) -> None:
        await self.connection.send(message)

    async def recv(self) -> str | bytes:
        return await self.connection.recv()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self.connection.close(code=code, reason=reason)

    def __aiter__(self) -> AsyncIterator[str | bytes]:
        return self.connection.__aiter__()
