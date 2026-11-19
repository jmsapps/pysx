"""Production configuration, verified TLS/WSS termination and safe telemetry."""

import asyncio
import json
import logging
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from browser_security_fixture import app, principal
from security_proxy import get, proxy
from websockets.asyncio.client import connect
from websockets.typing import Origin

from pysx import Application, Fragment, HostConfig, SecurityPolicy
from pysx.hosting.hosting import request_from_headers

if TYPE_CHECKING:
    from pathlib import Path

    from pysx import RequestEvent


@pytest.mark.parametrize(
    "changes",
    ["cookie", "development", "http", "authorization"],
)
def test_deployment_security_rejects_insecure_configuration(changes: str) -> None:
    config = HostConfig(
        production=True,
        secure_cookies=True,
        security=SecurityPolicy(
            origins=("https://app.test",),
            require_principal=True,
            authorize=lambda _request, _action, _target: True,
        ),
    )
    with pytest.raises(ValueError, match="production requires"):
        insecure(config, changes)


def insecure(config: HostConfig, changes: str) -> HostConfig:
    if changes == "cookie":
        return replace(config, secure_cookies=False)

    if changes == "development":
        return replace(config, development=True)

    if changes == "http":
        return replace(config, security=replace(config.security, origins=("http://app.test",)))

    return replace(config, security=replace(config.security, authorize=None))


@pytest.mark.parametrize(
    ("cookie", "expected"),
    [
        ("fixture_user=alice", "initial"),
        ("fixture_user=alice; fixture_login=", "initial"),
        ("fixture_user=alice; fixture_login=second-login", "second-login"),
    ],
)
def test_deployment_security_login_generation_defaults_without_a_cookie_value(
    cookie: str, expected: str
) -> None:
    """A present-but-empty cookie is no login generation; an empty one is never valid."""
    identity = principal(request_from_headers("/", [("cookie", cookie)]))
    assert identity is not None
    assert identity.principal == "alice"
    assert identity.session_id == expected


def test_deployment_security_tls_deep_link_wss_resume_and_cookie(tmp_path: Path) -> None:
    async def exercise() -> None:
        events: list[RequestEvent] = []
        application = Application(app, config=HostConfig(base_path="/secure", principal=principal))
        async with (
            application.listen(port=0) as server,
            proxy(tmp_path, server.sockets[0].getsockname()[1]) as (port, tls),
        ):
            external = f"https://127.0.0.1:{port}"
            application.config = HostConfig(
                base_path="/secure",
                production=True,
                secure_cookies=True,
                principal=principal,
                observe=events.append,
                security=SecurityPolicy(
                    origins=(external,),
                    require_principal=True,
                    trusted_proxies=("127.0.0.1/32",),
                    authorize=lambda _request, _action, _target: True,
                ),
            )
            status, headers, body = await get(port, tls, "/secure/deep/link")
            assert status == 200
            assert "alice" in body
            assert "Secure" in headers["set-cookie"]
            assert "HttpOnly" in headers["set-cookie"]
            cookie = "fixture_user=alice; " + headers["set-cookie"].split(";", 1)[0]
            assert (await get(port, tls, "/secure/", "fixture_user=alice; pysx_browser=invalid"))[
                0
            ] == 403
            assert (await get(port, tls, "/secure/", "fixture_user=mallory"))[0] == 403
            assert (
                await application.http(
                    request_from_headers(
                        "/secure/",
                        [
                            ("host", f"127.0.0.1:{port}"),
                            ("x-forwarded-host", f"127.0.0.1:{port}"),
                            ("x-forwarded-proto", "https"),
                            ("cookie", cookie),
                        ],
                        peer="192.0.2.10",
                    )
                )
            ).status == 403
            uri = f"wss://127.0.0.1:{port}/secure/ws?protocol=3"
            async with connect(
                uri, ssl=tls, origin=Origin(external), additional_headers={"Cookie": cookie}
            ) as socket:
                await socket.send('{"t":"hello","version":3}')
                first = json.loads(await socket.recv())
                assert first["t"] == "init"
            await asyncio.sleep(0.03)
            async with connect(
                uri + "&resume=1",
                ssl=tls,
                origin=Origin(external),
                additional_headers={"Cookie": cookie},
            ) as socket:
                await socket.send(
                    json.dumps(
                        {
                            "t": "hello",
                            "version": 3,
                            "resume": first["resume"],
                            "epoch": first["epoch"],
                            "state_rev": first["state_rev"],
                        }
                    )
                )
                restored = json.loads(await socket.recv())
                assert restored["t"] == "repair"
                assert restored["epoch"] == first["epoch"]
            assert events
            assert all(event.operation in ("http", "websocket") for event in events)
            assert all(len(event.request_id) == 32 for event in events)
        assert not application.runtime.graphs
        assert not application.connections

    asyncio.run(exercise())


def test_deployment_security_redacts_logs_observer_errors_and_metrics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def exercise() -> None:
        def fail() -> Fragment:
            raise ValueError("private-payload-token")

        def observer(_event: RequestEvent) -> None:
            raise ValueError("private-observer-token")

        application = Application(fail, config=HostConfig(observe=observer))
        result = await application.http(
            request_from_headers(
                "/?token=secret-query",
                [("cookie", "pysx_browser=secret-cookie"), ("authorization", "Bearer secret-auth")],
            )
        )
        assert result.status == 500
        assert len(dict(result.headers)["x-request-id"]) == 32
        assert application.metrics

        for private in (
            "private-payload-token",
            "private-observer-token",
            "secret-query",
            "secret-cookie",
            "secret-auth",
        ):
            assert private not in caplog.text
            assert private not in repr(application.metrics)
        assert any(
            record.name == "pysx.access" and hasattr(record, "request_id")
            for record in caplog.records
        )
        await application.close()

    caplog.set_level(logging.INFO)
    asyncio.run(exercise())


def test_nginx_proxy_accepts_forwarded_identity_from_a_trusted_proxy() -> None:
    """Behind a configured proxy, the public origin is what the application validates."""
    import ssl
    import tempfile
    from pathlib import Path

    from nginx_proxy import application, free_port, nginx

    port = free_port()
    public = f"https://localhost:{port}"

    async def exercise(proxied: int) -> tuple[bool, bool]:
        context = ssl._create_unverified_context()

        async def attempt(origin: str) -> bool:
            try:
                async with connect(
                    f"wss://localhost:{proxied}/ws",
                    origin=Origin(origin),
                    ssl=context,
                    open_timeout=30,
                ):
                    return True
            except Exception:
                return False

        return await attempt(public), await attempt("https://attacker.example")

    with (
        tempfile.TemporaryDirectory() as directory,
        application("examples.nginx:example", environment={"PYSX_ORIGIN": public}) as upstream,
        nginx(Path(directory), upstream, port=port) as proxied,
    ):
        accepted, forged = asyncio.run(exercise(proxied))
    assert accepted, "the configured public origin is accepted through the proxy"
    assert not forged, "another origin is still rejected behind the same proxy"


def test_nginx_proxy_rejects_forwarded_headers_from_an_untrusted_peer() -> None:
    """Explicit proxy trust is preserved, and trust is the only thing that differs here.

    Forwarding a host that does not match the configured origin is refused either way,
    so such a fixture would pass without proving anything about trust. This one forwards
    the configured public host and varies only whether the peer is a trusted proxy.
    """
    import urllib.error
    import urllib.request

    from nginx_proxy import application

    def attempt(trusted: str) -> int:
        with application(
            "examples.nginx:example",
            environment={
                "PYSX_ORIGIN": "https://public.example",
                "PYSX_TRUSTED_PROXIES": trusted,
            },
        ) as upstream:
            request = urllib.request.Request(
                f"http://127.0.0.1:{upstream}/",
                headers={"X-Forwarded-Host": "public.example", "X-Forwarded-Proto": "https"},
            )

            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return int(response.status)
            except urllib.error.HTTPError as error:
                return int(error.code)

    assert attempt("") >= 400, "forwarded identity from an untrusted peer is refused"
    assert attempt("127.0.0.1/32") == 200, "the same headers are honoured from a trusted proxy"
