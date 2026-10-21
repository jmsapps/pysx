"""Fail-closed network policy, authorization and single-owner graph isolation."""

import asyncio
import json
import logging
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from test_connection_pump import FakeConnection

from pysx import (
    AccessDenied,
    Application,
    Fragment,
    HostConfig,
    SecurityPolicy,
    current_request,
    native,
    on_cleanup,
    resource,
    signal,
)
from pysx.hosting.hosting import request_from_headers

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from pysx.hosting.asgi import Event
    from pysx.hosting.request import RequestContext


async def wait_for(check: Callable[[], bool]) -> None:
    async with asyncio.timeout(3):
        while not check():
            await asyncio.sleep(0.001)


def request(user: str = "alice", *, origin: str = "https://app.test") -> RequestContext:
    return request_from_headers(
        "/",
        [("host", "app.test"), ("origin", origin), ("x-user", user), ("cookie", "pysx_browser=b1")],
        scheme="https",
        peer="192.0.2.10",
    )


def host(**kwargs: object) -> Application:
    policy = SecurityPolicy(origins=("https://app.test",), require_principal=True, **kwargs)  # type: ignore[arg-type]

    return Application(
        lambda: native.P(current_request().principal or "anonymous"),
        config=HostConfig(principal=lambda req: req.headers.get("x-user"), security=policy),
    )


@pytest.mark.parametrize(
    "bad_origin",
    [
        "https://evil.test",
        "null",
        "https://app.test.evil",
        "https://app.test, https://evil.test",
        "https://app.test/path",
    ],
)
def test_principal_origin_denied_http_and_upgrade_without_allocation(bad_origin: str) -> None:
    async def exercise() -> None:
        application = host()
        response = await application.http(request(origin=bad_origin))
        assert response.status == 403
        assert not application.runtime.graphs
        connection = FakeConnection("/ws", [])
        connection.request = replace(request(origin=bad_origin), path="/ws")
        await application.websocket(connection)
        assert connection.closed == (1008, "access denied")
        assert not connection.sent
        assert not application.connections
        assert not application.runtime.graphs
        await application.close()

    asyncio.run(exercise())


def test_principal_origin_local_default_and_proxy_spoofing() -> None:
    application = Application(lambda: native.P("local"))

    for headers, peer in [
        ([("host", "evil.test")], "127.0.0.1"),
        ([("host", "localhost")], "192.0.2.1"),
        ([("host", "localhost"), ("x-forwarded-host", "app.test")], "127.0.0.1"),
    ]:
        with pytest.raises(AccessDenied, match="access denied"):
            application.prepare(request_from_headers("/", headers, peer=peer))
    proxied = host(trusted_proxies=("127.0.0.1/32",))
    context = proxied.prepare(
        request_from_headers(
            "/",
            [
                ("host", "localhost"),
                ("x-forwarded-host", "app.test"),
                ("x-forwarded-proto", "https"),
                ("origin", "https://app.test"),
                ("x-user", "alice"),
            ],
            peer="127.0.0.1",
        ),
        websocket=True,
    )
    assert context.scheme == "https"
    assert context.principal == "alice"
    with pytest.raises(AccessDenied):
        proxied.prepare(
            request_from_headers(
                "/",
                [("host", "localhost"), ("x-forwarded-proto", "https,http"), ("x-user", "alice")],
                peer="127.0.0.1",
            )
        )


def test_principal_origin_takeover_replay_epoch_and_logout() -> None:
    async def exercise() -> None:
        application = host()
        response = await application.http(request())
        assert response.adoption is not None
        alice = application.prepare(request())
        bob = application.prepare(request("bob"))
        with pytest.raises(ValueError, match="adoption handle"):
            application.runtime.claim(response.adoption, bob, "b1")
        session = application.runtime.claim(response.adoption, alice, "b1")
        with pytest.raises(ValueError, match="adoption handle"):
            application.runtime.claim(response.adoption, alice, "b1")
        token = application.runtime.attach(session, alice)
        application.runtime.detach(session)

        for context, epoch in [(bob, session.transport.epoch), (alice, "wrong")]:
            with pytest.raises(ValueError, match="resume handle"):
                application.runtime.resume(token, context, epoch, 0)
        claims = await asyncio.gather(
            *(claim(application, token, alice, session.transport.epoch) for _ in range(2))
        )
        assert claims.count(True) == 1
        rotated = application.runtime.attach(session, alice)
        application.runtime.detach(session)
        pending = await application.http(request())
        other = await application.http(request("bob"))
        application.revoke("alice")
        assert session.closed
        assert pending.adoption not in application.runtime.pending
        assert other.adoption in application.runtime.pending
        with pytest.raises(ValueError, match="resume handle"):
            application.runtime.resume(rotated, alice, session.transport.epoch, 0)
        await application.close()

    asyncio.run(exercise())


async def claim(application: Application, token: str, context: RequestContext, epoch: str) -> bool:
    try:
        application.runtime.resume(token, context, epoch, 0)
    except ValueError:
        return False

    return True


@pytest.mark.parametrize("cookie", [r'pysx_browser="\015\012evil"', 'pysx_browser="evil; Path=/"'])
def test_principal_origin_decoded_cookie_cannot_inject_response_headers(cookie: str) -> None:
    async def exercise() -> None:
        application = Application(lambda: native.P("private"))
        result = await application.http(request_from_headers("/", [("cookie", cookie)]))
        assert result.status == 403
        assert "set-cookie" not in dict(result.headers)
        assert not application.runtime.graphs
        await application.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("action", ["event", "command", "revoked", "principal_changed"])
def test_principal_origin_active_authorization_rechecks(action: str) -> None:
    async def exercise() -> None:
        denied: set[str] = set()
        identity = ["alice"]
        clicks = signal(0)
        application = Application(
            lambda: native.Button("click", on_click=lambda _event: clicks.set(clicks() + 1)),
            config=HostConfig(
                principal=lambda _req: identity[0],
                security=SecurityPolicy(
                    authorize=lambda _req, operation, _target: operation not in denied
                ),
            ),
        )
        connection = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        pump = asyncio.create_task(application.websocket(connection))
        await wait_for(lambda: bool(connection.frames("init")))
        session = next(iter(application.runtime.graphs))

        if action == "revoked":
            application.revoke("alice")
        else:
            if action == "principal_changed":
                identity[0] = "bob"
            else:
                denied.add(action)
            connection.incoming.put_nowait(
                json.dumps(
                    {
                        "t": "event",
                        "version": 3,
                        "epoch": session.transport.epoch,
                        "state_rev": session.transport.revision,
                        "seq": 1,
                        "h": next(iter(session.rendered.handlers)),
                    }
                )
            )
        await wait_for(lambda: connection.closed is not None)
        outcomes = await asyncio.gather(pump, return_exceptions=True)
        assert all(result is None or isinstance(result, AccessDenied) for result in outcomes)
        assert connection.closed == (1008, "access denied")
        assert clicks() == (1 if action == "command" else 0)
        assert not application.runtime.graphs
        assert not application.runtime.resume_handles
        await application.close()

    asyncio.run(exercise())


def test_principal_origin_callback_failure_and_resource_denial() -> None:
    async def exercise() -> None:
        def broken(_request: RequestContext) -> str:
            raise ValueError("private credential")

        application = Application(lambda: native.P("private"), config=HostConfig(principal=broken))
        result = await application.http(request())
        assert result.status == 403
        assert result.body == b"access denied"
        assert not application.runtime.graphs
        await application.close()
        loaded: list[int] = []

        async def loader() -> int:
            loaded.append(1)

            return 42

        def app() -> Fragment:
            resource("private-data", loader)

            return native.P("safe")

        application = Application(
            app,
            config=HostConfig(
                security=SecurityPolicy(
                    authorize=lambda _req, action, _target: action != "resource"
                )
            ),
        )
        assert (await application.http(request_from_headers("/", []))).status == 200
        await asyncio.sleep(0.03)
        assert not loaded
        await application.close()

    asyncio.run(exercise())


def test_principal_origin_asgi_denial_before_accept() -> None:
    async def exercise() -> None:
        application = host()
        sent: list[Event] = []

        async def receive() -> Event:
            return {"type": "websocket.connect"}

        async def send(event: Event) -> None:
            sent.append(event)

        await application.asgi()(
            {
                "type": "websocket",
                "path": "/ws",
                "scheme": "wss",
                "headers": [(b"host", b"app.test"), (b"origin", b"https://evil.test")],
                "state": {"pysx_principal": "alice"},
            },
            receive,
            send,
        )
        assert sent == [{"type": "websocket.close", "code": 1008, "reason": "access denied"}]
        assert not application.handshakes
        assert not application.runtime.graphs
        await application.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("chained", [False, True])
def test_principal_origin_checks_every_edit_and_chained_handler(chained: bool) -> None:
    async def exercise() -> None:
        text = signal("private")
        writes: list[object] = []
        denied: set[str] = set()
        application = Application(
            lambda: native.Main(
                native.Input(bind_value=text), native.Button("write", on_click=writes.append)
            ),
            config=HostConfig(
                security=SecurityPolicy(
                    authorize=lambda _req, _action, target: target not in denied
                )
            ),
        )
        connection = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        pump = asyncio.create_task(application.websocket(connection))
        await wait_for(lambda: bool(connection.frames("init")))
        session = next(iter(application.runtime.graphs))
        binding = next(iter(session.rendered.bindings))
        button = next(handler for handler in session.rendered.handlers if handler != binding)
        denied.add(button if chained else binding)
        message: dict[str, object] = {
            "t": "event",
            "version": 3,
            "epoch": session.transport.epoch,
            "state_rev": session.transport.revision,
            "seq": 1,
            "h": binding if chained else button,
        }

        if chained:
            message["after"] = button
            message["v"] = "stolen"
        else:
            message["edits"] = [{"h": binding, "v": "stolen", "rev": 1}]
        connection.incoming.put_nowait(json.dumps(message))
        await asyncio.wait_for(pump, 3)
        assert connection.closed == (1008, "access denied")
        assert text() == "private"
        assert not writes
        await application.close()

    asyncio.run(exercise())


def test_principal_origin_revocation_finishes_after_cleanup_failure() -> None:
    async def exercise() -> None:
        def fail() -> None:
            raise ValueError("private cleanup")

        def app() -> Fragment:
            on_cleanup(fail)

            return native.P("private")

        application = Application(app, config=HostConfig(principal=lambda _req: "alice"))
        await application.http(request_from_headers("/", []))
        await application.http(request_from_headers("/", []))
        with pytest.raises(ExceptionGroup, match="revocation cleanup"):
            application.revoke("alice")
        assert not application.runtime.pending
        assert not application.runtime.graphs
        await application.close()

    asyncio.run(exercise())


def production_host(authorize: Callable[[RequestContext, str, str], bool]) -> Application:
    return Application(
        lambda: native.P(current_request().principal or "anonymous"),
        config=HostConfig(
            production=True,
            secure_cookies=True,
            principal=lambda req: req.headers.get("x-user"),
            security=SecurityPolicy(
                origins=("https://app.test",), require_principal=True, authorize=authorize
            ),
        ),
    )


def production_request(path: str) -> RequestContext:
    return request_from_headers(
        path,
        [
            ("host", "app.test"),
            ("origin", "https://app.test"),
            ("x-user", "alice"),
            ("cookie", "pysx_browser=" + "b" * 32),
        ],
        scheme="https",
        peer="192.0.2.10",
    )


def test_principal_origin_probe_and_client_asset_skip_application_authorization() -> None:
    async def exercise() -> None:
        asked: list[tuple[str, str]] = []

        def authorize(_request: RequestContext, action: str, target: str) -> bool:
            asked.append((action, target))

            return False

        application = production_host(authorize)
        probe = await application.http(
            request_from_headers("/health", [("host", "10.0.0.7:8000")], peer="10.0.0.1")
        )
        assert (probe.status, probe.body) == (200, b"ok")
        asset = await application.http(production_request("/client.js"))
        assert asset.status == 200
        assert (await application.http(production_request("/"))).status == 403
        assert asked == [("http", "/")]
        await application.close()

    asyncio.run(exercise())


def test_principal_origin_trusted_proxy_may_forward_client_address() -> None:
    proxied = host(trusted_proxies=("127.0.0.1/32",))
    forwarded = [
        ("host", "localhost"),
        ("x-forwarded-host", "app.test"),
        ("x-forwarded-proto", "https"),
        ("x-forwarded-for", "203.0.113.9, 127.0.0.1"),
        ("x-forwarded-port", "443"),
        ("origin", "https://app.test"),
        ("x-user", "alice"),
    ]
    context = proxied.prepare(request_from_headers("/", forwarded, peer="127.0.0.1"))
    assert (context.peer, context.scheme, context.principal) == ("127.0.0.1", "https", "alice")

    with pytest.raises(AccessDenied):
        proxied.prepare(request_from_headers("/", forwarded, peer="192.0.2.10"))


def test_principal_origin_middleware_identity_coexists_with_host_resolver() -> None:
    async def exercise() -> None:
        application = host()
        result = await application.http(replace(request(), principal="carol"))
        assert result.status == 200
        assert b"carol" in result.body
        await application.close()

    asyncio.run(exercise())


def test_principal_origin_failing_authorizer_is_reported_without_leaking() -> None:
    async def exercise() -> None:
        def broken(_request: RequestContext, _action: str, _target: str) -> bool:
            raise RuntimeError("auth backend unavailable")

        application = production_host(broken)

        with caplog_errors() as records:
            result = await application.http(production_request("/"))
        assert (result.status, result.body) == (403, b"access denied")
        assert [record.phase for record in records] == ["handler"]  # type: ignore[attr-defined]
        assert all("auth backend" not in record.getMessage() for record in records)
        assert application.metrics["http:denied"] == 1
        await application.close()

    asyncio.run(exercise())


class CollectingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextmanager
def caplog_errors() -> Generator[list[logging.LogRecord]]:
    handler = CollectingHandler()
    logger = logging.getLogger("pysx.errors")
    logger.addHandler(handler)

    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)


def test_principal_origin_graceful_disposal_is_not_counted_as_denial() -> None:
    async def exercise() -> None:
        application = Application(lambda: native.P("live"))
        connection = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        pump = asyncio.create_task(application.websocket(connection))
        await wait_for(lambda: bool(connection.frames("init")))
        session = next(iter(application.runtime.graphs))
        application.runtime.release(session)
        session.changed.set()
        await wait_for(lambda: connection.closed is not None)
        await asyncio.gather(pump, return_exceptions=True)
        assert connection.closed == (1001, "session closed")
        assert application.metrics == {"websocket:success": 1}
        await application.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_principal_origin_probe_answers_infrastructure_methods(method: str) -> None:
    async def exercise() -> None:
        application = production_host(lambda _request, _action, _target: False)
        probe = await application.http(
            request_from_headers("/health", [("host", "10.0.0.7:8000")], peer="10.0.0.1"),
            method=method,
        )
        assert probe.status == 200
        assert probe.body == (b"" if method == "HEAD" else b"ok")
        assert dict(probe.headers)["content-length" if method == "HEAD" else "cache-control"]
        await application.close()

    asyncio.run(exercise())
