"""Shared application configuration and HTTP/connection ownership for both hosts."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import mimetypes
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from http import HTTPStatus
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, cast
from urllib.parse import quote, urlsplit

from .connection import bound_connection, serve_connection
from .errors import ErrorInfo, report_error
from .http import HTTPResult, HTTPRuntime
from .request import RequestContext, browser_id
from .routing import Location
from .transport import TransportLimits

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
    render_capacity: int = 100
    render_ttl: float = 30
    mutation_capacity: int = 64
    task_capacity: int = 1024
    development: bool = False
    secure_cookies: bool = False
    health_path: str | None = "/health"
    shutdown_timeout: float = 5
    transport: TransportLimits = field(default_factory=TransportLimits)
    principal: Callable[[RequestContext], str | None] | None = None
    assets: Mapping[str, StaticAsset] = field(default_factory=lambda: dict[str, StaticAsset]())
    startup: tuple[Hook, ...] = ()
    shutdown: tuple[Hook, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(cast("object", self.transport), TransportLimits):
            raise ValueError("transport must be TransportLimits")

        for name in ("render_capacity", "mutation_capacity", "task_capacity"):
            value = getattr(self, name)

            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")

        for name in ("render_ttl", "shutdown_timeout"):
            value = getattr(self, name)

            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        location = Location.parse(self.base_path or "/")

        if (
            location.search
            or location.hash
            or self.shutdown_timeout <= 0
            or self.mutation_capacity < 1
            or self.task_capacity < 1
        ):
            raise ValueError("invalid host prefix, shutdown timeout or capacity")

        if self.health_path is not None and (
            not isinstance(cast("object", self.health_path), str)
            or not self.health_path.startswith("/")
            or self.health_path.rstrip("/") == ""
        ):
            raise ValueError("health_path must be None or an absolute non-root path")

        if self.health_path is not None:
            object.__setattr__(self, "health_path", self.health_path.rstrip("/"))
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
            limits=self.config.transport,
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
        self.close_lock = asyncio.Lock()
        self.connections: dict[int, tuple[Connection, asyncio.Task[object]]] = {}
        self.handshakes: dict[int, tuple[Connection, asyncio.Task[object]]] = {}
        self.start_tasks: set[asyncio.Task[object]] = set()

    def path(self, path: str) -> str:
        return self.config.base_path + "/" + path.lstrip("/")

    def prepare(self, request: RequestContext) -> RequestContext:
        context = replace(request, base_path=request.base_path or self.config.base_path)

        if context.principal is None and self.config.principal is not None:
            context = replace(context, principal=self.config.principal(context))

        return context

    async def start(self) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.start_tasks.add(task)

        try:
            await self._start()
        finally:
            self.start_tasks.discard(task)

    async def _start(self) -> None:
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
        elif self.config.health_path is not None and local == self.config.health_path:
            result = HTTPResult(200, (("content-type", "text/plain; charset=utf-8"),), b"ok")
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

        bounded = bound_connection(connection, self.config.transport)
        admission_key = getattr(connection, "admission_key", id(connection))
        self.handshakes.pop(admission_key, None)

        if self.closed or len(self.connections) >= self.config.transport.graphs:
            await bounded.close(1013, "connection capacity")

            if bounded.reserved:
                self.runtime.unreserve()

            return

        if urlsplit(connection.request.path).path != connection.request.base_path + "/ws":
            await bounded.close(code=1008, reason="invalid socket path")

            if bounded.reserved:
                self.runtime.unreserve()

            return
        task = asyncio.current_task()
        assert task is not None
        identifier = id(connection)
        self.connections[identifier] = (bounded, task)

        try:
            await serve_connection(self.runtime, bounded)
        finally:
            self.connections.pop(identifier, None)

    def admit_handshake(self, identifier: int, connection: Connection) -> bool:
        if (
            self.closed
            or len(self.handshakes) + len(self.connections) >= self.config.transport.graphs
        ):
            return False
        task = asyncio.current_task()
        assert task is not None
        self.handshakes[identifier] = (connection, task)

        return True

    async def close(self) -> None:
        try:
            async with asyncio.timeout(self.config.shutdown_timeout):
                async with self.close_lock:
                    await self._close()
        except (TimeoutError, asyncio.CancelledError) as error:
            self.closed = True

            for task in self.start_tasks:
                task.cancel()

            for connection, task in (*self.connections.values(), *self.handshakes.values()):
                bound_connection(connection, self.config.transport).abort()
                task.cancel()
            self.runtime.close()
            await asyncio.sleep(0)

            if isinstance(error, asyncio.CancelledError):
                raise

            raise TimeoutError("application shutdown deadline") from None

    async def _close(self) -> None:
        async with self.start_lock:
            if self.closed:
                return
            self.closed = True
        errors: list[Exception] = []
        owned_tasks = self.runtime.tasks()

        active = (*self.connections.values(), *self.handshakes.values())

        if active:
            outcomes = await asyncio.gather(
                *(
                    bound_connection(connection, self.config.transport).close(
                        1001, "application shutdown"
                    )
                    for connection, _task in active
                ),
                return_exceptions=True,
            )
            errors.extend(outcome for outcome in outcomes if isinstance(outcome, Exception))
        current = asyncio.current_task()
        tasks = {task for _connection, task in active if task is not current} | owned_tasks

        for task in tasks:
            task.cancel()

        try:
            self.runtime.close()
        except Exception as error:
            errors.append(error)

        tasks.update(self.runtime.tasks())

        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=self.config.shutdown_timeout)

            if pending:
                raise TimeoutError("owned task shutdown deadline")

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
        protocols: set[ServerConnection] = set()

        class HTTPConnection(ServerConnection):
            # The websocket handshake parser accepts only GET. Keep the HTTP
            # method separately before passing the request through that parser.
            method: str = "GET"
            first_line: bytes = b""
            reading_line: bool = True
            reserved: bool = False
            admitted: bool = False

            def connection_made(self, transport: asyncio.BaseTransport) -> None:
                protocols.add(self)
                super().connection_made(transport)

            def connection_lost(self, exc: Exception | None) -> None:
                protocols.discard(self)

                if self.reserved:
                    self.reserved = False
                    application.runtime.unreserve()

                if self.admitted:
                    self.admitted = False
                    application.handshakes.pop(id(self), None)
                super().connection_lost(exc)

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

        application = self

        async def process_request(
            _connection: ServerConnection, request: Request
        ) -> Response | None:
            context = request_from_headers(
                request.path, list(request.headers.raw_items()), base_path=self.config.base_path
            )

            method = _connection.method if isinstance(_connection, HTTPConnection) else "GET"

            if method == "GET" and urlsplit(request.path).path == self.path("ws"):
                from urllib.parse import parse_qs

                if not isinstance(_connection, HTTPConnection):
                    raise RuntimeError("missing admission adapter")

                adapter = _StandaloneConnection(_connection, context)

                if not self.admit_handshake(id(_connection), adapter):
                    return Response(503, "Service Unavailable", Headers(), b"connection capacity")
                _connection.admitted = True
                adoption = parse_qs(urlsplit(request.path).query).get("adoption")
                resume = parse_qs(urlsplit(request.path).query).get("resume") == ["1"]

                if not adoption and not resume:
                    if not self.runtime.reserve():
                        return Response(503, "Service Unavailable", Headers(), b"graph capacity")
                    _connection.reserved = True

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
            reserved = False

            if isinstance(connection, HTTPConnection):
                reserved, connection.reserved = connection.reserved, False

            adapter = _StandaloneConnection(connection, context, reserved=reserved)

            try:
                await self.websocket(adapter)
            finally:
                self.handshakes.pop(id(connection), None)

                if reserved and self.runtime.reservations and adapter.reserved:
                    self.runtime.unreserve()

        try:
            server = await serve(
                handler,
                host,
                port,
                process_request=process_request,
                create_connection=HTTPConnection,
                close_timeout=self.config.transport.close_timeout,
                open_timeout=self.config.transport.hello_timeout,
                ping_interval=self.config.transport.ping_interval,
                ping_timeout=self.config.transport.ping_timeout,
                max_size=self.config.transport.max_frame_bytes,
                max_queue=self.config.transport.socket_queue,
                write_limit=self.config.transport.write_limit,
            )
        except BaseException:
            await self.close()

            raise

        try:
            yield server
        finally:
            server.close(close_connections=False)
            shutdown_error: BaseException | None = None

            try:
                async with asyncio.timeout(self.config.shutdown_timeout):
                    try:
                        await self.close()
                    except BaseException as error:
                        shutdown_error = error
                    finally:
                        for protocol in tuple(protocols):
                            protocol.transport.abort()
                    await server.wait_closed()
            finally:
                for protocol in tuple(protocols):
                    protocol.transport.abort()

            if shutdown_error is not None:
                raise shutdown_error


class _StandaloneConnection:
    def __init__(
        self, connection: ServerConnection, request: RequestContext, *, reserved: bool = False
    ) -> None:
        self.connection = connection
        self.request = request
        self.reserved = reserved
        self.admission_key = id(connection)

    def abort(self) -> None:
        self.connection.transport.abort()

    async def send(self, message: str) -> None:
        await self.connection.send(message)

    async def recv(self) -> str | bytes:
        return await self.connection.recv()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self.connection.close(code=code, reason=reason)

    def __aiter__(self) -> AsyncIterator[str | bytes]:
        return self.connection.__aiter__()
