"""Shared application configuration and HTTP/connection ownership for both hosts."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import mimetypes
import re
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from http import HTTPStatus
from importlib.resources import files
from pathlib import Path  # noqa: TC003 - public from_path annotations resolve at runtime
from types import MappingProxyType
from typing import TYPE_CHECKING, cast
from urllib.parse import quote, urlsplit

from pysx.components.errors import ErrorInfo, report_error
from pysx.runtime.connection import bound_connection, serve_connection
from pysx.runtime.routing import Location
from pysx.runtime.transport import TransportError, TransportLimits

from .http import HTTPResult, HTTPRuntime
from .request import AuthenticatedIdentity, RequestContext, browser_id
from .security import AccessDenied, SecurityPolicy, origin
from .telemetry import RequestEvent, record

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping
    from string.templatelib import Template

    from websockets.asyncio.server import Server, ServerConnection
    from websockets.http11 import Request, Response

    from pysx.rendering.render import Fragment
    from pysx.runtime.capsule import CapsuleIssuer
    from pysx.runtime.connection import Connection

    from .asgi import ASGI

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
    principal: Callable[[RequestContext], AuthenticatedIdentity | str | None] | None = None
    security: SecurityPolicy = field(default_factory=SecurityPolicy)
    production: bool = False
    observe: Callable[[RequestEvent], None] | None = None
    assets: Mapping[str, StaticAsset] = field(default_factory=lambda: dict[str, StaticAsset]())
    startup: tuple[Hook, ...] = ()
    shutdown: tuple[Hook, ...] = ()
    recovery_namespace: str | None = None
    recovery_key_directory: Path | None = None

    def __post_init__(self) -> None:
        if self.production and (
            self.development
            or not self.secure_cookies
            or not self.security.origins
            or not self.security.require_principal
            or self.security.authorize is None
            or any(not value.startswith("https://") for value in self.security.origins)
        ):
            raise ValueError(
                "production requires secure cookies, HTTPS origins, required principal "
                "and authorization; development must be disabled"
            )

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

        if location.search or location.hash:
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
    path: str,
    headers: list[tuple[str, str]],
    *,
    base_path: str = "",
    principal: str | None = None,
    scheme: str = "http",
    peer: str | None = None,
    authentication_session: str | None = None,
) -> RequestContext:
    combined: dict[str, str] = {}

    for name, value in headers:
        key = name.lower()
        separator = "; " if key == "cookie" else ", "
        combined[key] = combined[key] + separator + value if key in combined else value

    return RequestContext(
        path=path,
        headers=MappingProxyType(combined),
        principal=principal,
        base_path=base_path,
        scheme=scheme,
        peer=peer,
        authentication_session=authentication_session,
    )


class Application:
    def __init__(
        self, app: Callable[[], Template | Fragment], *, config: HostConfig | None = None
    ) -> None:
        self.app = app
        self.config = config or HostConfig()
        self.recovery_issuer: CapsuleIssuer | None = None
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
                files("pysx").joinpath("static/client.js").read_bytes(),
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
        self.metrics: dict[str, int] = {}

    def path(self, path: str) -> str:
        return self.config.base_path + "/" + path.lstrip("/")

    def prepare(self, request: RequestContext, *, websocket: bool = False) -> RequestContext:
        context = replace(request, base_path=request.base_path or self.config.base_path)

        try:
            if any(
                any(character in value for character in "\r\n\0")
                for value in context.headers.values()
            ):
                raise AccessDenied
            forwarded = {
                name
                for name in context.headers
                if name == "forwarded" or name.startswith("x-forwarded-")
            }

            if forwarded:
                if not self.config.security.trusts(context.peer) or forwarded - {
                    "x-forwarded-host",
                    "x-forwarded-proto",
                    "x-forwarded-for",
                    "x-forwarded-port",
                }:
                    raise AccessDenied
                scheme = context.headers.get("x-forwarded-proto", context.scheme)

                if scheme not in ("http", "https"):
                    raise AccessDenied
                headers = dict(context.headers)
                headers["host"] = headers.get("x-forwarded-host", headers.get("host", ""))
                context = replace(context, headers=MappingProxyType(headers), scheme=scheme)

            resolved = context.principal is None and self.config.principal is not None

            if resolved:
                assert self.config.principal is not None
                identity = self.config.principal(context)
                context = replace(
                    context,
                    principal=identity.principal
                    if isinstance(identity, AuthenticatedIdentity)
                    else identity,
                    authentication_session=identity.session_id
                    if isinstance(identity, AuthenticatedIdentity)
                    else None,
                )

            if context.principal is not None and (
                not isinstance(cast("object", context.principal), str) or not context.principal
            ):
                raise AccessDenied

            if context.authentication_session is not None and (
                type(context.authentication_session) is not str
                or not context.authentication_session
                or len(context.authentication_session) > 4096
                or context.principal is None
            ):
                raise AccessDenied
            self.config.security.check_network(context, websocket=websocket)

            browser = browser_id(context)

            if browser and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", browser):
                raise AccessDenied

            if self.config.production:
                if context.scheme != "https":
                    raise AccessDenied

                if browser and not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", browser):
                    raise AccessDenied

                if (
                    len(re.findall(r"(?:^|;)\s*pysx_browser=", context.headers.get("cookie", "")))
                    > 1
                ):
                    raise AccessDenied
        except AccessDenied:
            raise AccessDenied("access denied") from None
        except Exception as error:
            self.denial_failed(error)

            raise AccessDenied("access denied") from None

        def check(action: str, target: str) -> None:
            try:
                if (
                    resolved
                    and action != "command"
                    and self.config.principal is not None
                    and self.config.principal(context)
                    != (
                        AuthenticatedIdentity(context.principal, context.authentication_session)
                        if context.principal is not None
                        and context.authentication_session is not None
                        else context.principal
                    )
                ):
                    raise AccessDenied
                self.config.security.check(context, action, target)
            except AccessDenied:
                raise AccessDenied("access denied") from None
            except Exception as error:
                self.denial_failed(error)

                raise AccessDenied("access denied") from None

        return replace(context, access=check)

    def denial_failed(self, error: Exception) -> None:
        """A denial caused by a failing hook is a server fault, not a quiet rejection."""
        report_error(ErrorInfo(None, "handler"), error, development=self.config.development)

    def revoke(self, principal: str) -> None:
        """Dispose every pending, active and detached graph for a logged-out identity."""
        errors: list[Exception] = []

        for token, pending in tuple(self.runtime.pending.items()):
            if pending.principal == principal:
                try:
                    self.runtime.expire(token)
                except Exception as error:
                    errors.append(error)

        for session in tuple(self.runtime.graphs):
            if session.request.principal == principal:
                session.revoked = True

                try:
                    self.runtime.release(session)
                except Exception as error:
                    errors.append(error)
                finally:
                    session.changed.set()

        if errors:
            raise ExceptionGroup("revocation cleanup failed", errors)

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

            if self.runtime.owner_hint is None:
                # Keep compiler/checker imports free of the host's crypto startup cost.
                from pysx.components.composition import portable_identity_for
                from pysx.runtime.capsule import CapsuleIssuer

                from .browser_recovery import BrowserRecovery
                from .recovery_keys import default_directory, load_keys

                namespace = self.config.recovery_namespace or portable_identity_for(self.app)
                directory = self.config.recovery_key_directory or default_directory(namespace)
                self.recovery_issuer = CapsuleIssuer(
                    load_keys(directory, provision=self.config.recovery_key_directory is None)
                )
                self.runtime.browser_recovery = BrowserRecovery(self.recovery_issuer, namespace)

            for hook in self.config.startup:
                result = hook()

                if inspect.isawaitable(result):
                    await result
            self.started = True

    def observed(self, request: RequestContext, operation: str, status: int) -> None:
        event = RequestEvent(request.request_id, operation, status)
        outcome = (
            "denied"
            if status in (403, 1008)
            else "success"
            if status in (1000, 1001) or 200 <= status < 400
            else "failure"
        )
        key = f"{operation}:{outcome}"
        self.metrics[key] = self.metrics.get(key, 0) + 1
        record(event)

        if self.config.observe is not None:
            try:
                self.config.observe(event)
            except Exception:
                report_error(ErrorInfo(None, "handler"), RuntimeError("observer failed"))

    async def http(self, request: RequestContext, *, method: str = "GET") -> HTTPResult:
        try:
            result = await self._http(request, method=method)
        except Exception as error:
            report_error(ErrorInfo(None, "render"), error, development=self.config.development)
            result = HTTPResult(
                500, (("cache-control", "private, no-store"),), b"application failed"
            )
        self.observed(request, "http", result.status)

        return replace(result, headers=(*result.headers, ("x-request-id", request.request_id)))

    async def _http(self, request: RequestContext, *, method: str = "GET") -> HTTPResult:
        if self.closed:
            return HTTPResult(503, (), b"application closed")

        reset_path = (request.base_path or self.config.base_path) + "/__pysx__/reset"

        if method == "POST" and urlsplit(request.path).path == reset_path:
            await self.start()

            return self.reset_browser(request)

        if method not in ("GET", "HEAD"):
            return HTTPResult(405, (("allow", "GET, HEAD"),), b"method not allowed")
        await self.start()

        probe = request.base_path or self.config.base_path

        if (
            self.config.health_path is not None
            and urlsplit(request.path).path == probe + self.config.health_path
            and self.config.health_path not in self.assets
        ):
            headers = (("content-type", "text/plain; charset=utf-8"), ("cache-control", "no-store"))

            if method == "HEAD":
                return HTTPResult(200, (*headers, ("content-length", "2")), b"")

            return HTTPResult(200, headers, b"ok")

        try:
            context = self.prepare(request)
        except AccessDenied:
            return HTTPResult(403, (("cache-control", "private, no-store"),), b"access denied")
        path = urlsplit(context.path).path
        prefix = context.base_path

        if prefix and path != prefix and not path.startswith(prefix + "/"):
            return HTTPResult(404, (), b"not found")
        local = path[len(prefix) :] or "/"
        asset = self.assets.get(local)

        if asset is not None:
            result = asset.response(context.headers)
        else:
            try:
                assert context.access is not None
                context.access("http", path)
            except AccessDenied:
                return HTTPResult(403, (("cache-control", "private, no-store"),), b"access denied")
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

    def reset_browser(self, request: RequestContext) -> HTTPResult:
        """Reset anonymous browser custody without retaining a recovery denylist."""
        headers = (("cache-control", "private, no-store"),)

        try:
            context = self.prepare(request)

            if (
                self.runtime.browser_recovery is None
                or context.principal is not None
                or not browser_id(context)
                or context.headers.get("x-pysx-reset") != "1"
                or origin(context.headers.get("origin", ""))
                != origin(context.scheme + "://" + context.headers.get("host", ""))
            ):
                raise AccessDenied("access denied")
            assert context.access is not None
            context.access("reset", context.path)
        except AccessDenied, ValueError:
            return HTTPResult(403, headers, b"access denied")
        browser = browser_id(context)
        errors: list[Exception] = []

        for token, pending in tuple(self.runtime.pending.items()):
            if pending.browser == browser:
                try:
                    self.runtime.expire(token)
                except Exception as error:
                    errors.append(error)

        for session in tuple(self.runtime.graphs):
            if session.browser == browser:
                try:
                    self.runtime.release(session)
                except Exception as error:
                    errors.append(error)
                finally:
                    session.changed.set()
        cookie = f"pysx_browser={secrets.token_urlsafe(32)}; Path=/; HttpOnly; SameSite=Lax" + (
            "; Secure" if self.config.secure_cookies else ""
        )

        if errors:
            report_error(
                ErrorInfo(None, "cleanup"),
                ExceptionGroup("reset cleanup failed", errors),
                development=self.config.development,
            )

        return HTTPResult(
            500 if errors else 204,
            (*headers, ("set-cookie", cookie)),
            b"session reset incomplete" if errors else b"",
        )

    async def websocket(self, connection: Connection) -> None:
        from websockets.exceptions import ConnectionClosedOK

        await self.start()

        bounded = bound_connection(connection, self.config.transport)
        admission_key = getattr(connection, "admission_key", id(connection))
        self.handshakes.pop(admission_key, None)

        try:
            context = self.prepare(connection.request, websocket=True)
            assert context.access is not None
            context.access("websocket", "/ws")
            bounded.request = context
        except AccessDenied:
            await bounded.close(1008, "access denied")
            self.observed(connection.request, "websocket", 1008)

            if bounded.reserved:
                self.runtime.unreserve()
                bounded.reserved = False

            return

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
        except ConnectionClosedOK:
            pass
        except AccessDenied:
            await bounded.close(1008, "access denied")
        except TransportError:
            await bounded.close(1001, "session closed")
        except Exception as error:
            report_error(ErrorInfo(None, "handler"), error, development=self.config.development)
            await bounded.close(1011, "application failed")
        finally:
            self.connections.pop(identifier, None)
            self.observed(connection.request, "websocket", bounded.close_code or 1000)

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
        from websockets.asyncio.server import serve
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        from ._http_connection import HTTPMethodConnection

        await self.start()
        protocols: set[ServerConnection] = set()

        class HTTPConnection(HTTPMethodConnection):
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

        application = self

        async def process_request(
            _connection: ServerConnection, request: Request
        ) -> Response | None:
            context = request_from_headers(
                request.path,
                list(request.headers.raw_items()),
                base_path=self.config.base_path,
                peer=str(_connection.remote_address[0]) if _connection.remote_address else None,
            )

            method = _connection.method if isinstance(_connection, HTTPConnection) else "GET"

            if method == "GET" and urlsplit(request.path).path == self.path("ws"):
                from urllib.parse import parse_qs

                try:
                    context = self.prepare(context, websocket=True)
                    assert context.access is not None
                    context.access("websocket", "/ws")
                except AccessDenied:
                    self.observed(context, "websocket", 1008)

                    return Response(403, "Forbidden", Headers(), b"access denied")

                if not isinstance(_connection, HTTPConnection):
                    raise RuntimeError("missing admission adapter")

                adapter = _StandaloneConnection(_connection, context)

                if not self.admit_handshake(id(_connection), adapter):
                    return Response(503, "Service Unavailable", Headers(), b"connection capacity")
                _connection.admitted = True
                adoption = parse_qs(urlsplit(request.path).query).get("adoption")
                resume = parse_qs(urlsplit(request.path).query).get("resume") == ["1"]
                recover = parse_qs(urlsplit(request.path).query).get("recover") == ["1"]

                if not adoption and not resume and not recover:
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
            context = request_from_headers(
                request.path,
                list(request.headers.raw_items()),
                base_path=self.config.base_path,
                peer=str(connection.remote_address[0]) if connection.remote_address else None,
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
