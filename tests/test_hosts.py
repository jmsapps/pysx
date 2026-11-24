"""Shared host contracts and ASGI lifecycle/disconnect boundaries."""

import asyncio
import json
from typing import TYPE_CHECKING, cast

import pytest

from pysx import (
    Application,
    Fragment,
    HostConfig,
    StaticAsset,
    current_request,
    native,
    on_cleanup,
    pysx,
)
from pysx.hosting.hosting import request_from_headers
from pysx.hosting.request import Cookie, CookieError, SameSite

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysx.hosting.asgi import ASGI, Event
    from pysx.hosting.http import HTTPResult
    from pysx.hosting.request import RequestContext


async def http(
    adapter: ASGI, path: str, *, method: str = "GET", scope: Event | None = None
) -> list[Event]:
    sent: list[Event] = []

    async def receive() -> Event:
        return {"type": "http.request", "body": b""}

    async def send(event: Event) -> None:
        sent.append(event)

    await adapter(
        {"type": "http", "path": path, "method": method, "headers": [], **(scope or {})},
        receive,
        send,
    )

    return sent


def test_host_config_http_prefix_assets_methods_and_principal() -> None:
    async def exercise() -> None:
        cleaned: list[str | None] = []

        def app() -> Fragment:
            request = current_request()
            on_cleanup(lambda: cleaned.append(current_request().principal))

            return native.P(request.principal or "anonymous")

        application = Application(
            app,
            config=HostConfig(
                base_path="/demo/",
                principal=lambda request: request.headers.get("x-user"),
                assets={"/sample.bin": StaticAsset(b"\x00\xff", "application/octet-stream", 60)},
            ),
        )
        result = await application.http(request_from_headers("/demo/", [("X-User", "alice")]))
        assert b"alice" in result.body
        assert b"/demo/client.js" in result.body
        assert result.adoption in application.runtime.pending
        head = await application.http(request_from_headers("/demo/", []), method="HEAD")
        assert head.body == b""
        assert head.adoption is None
        assert len(application.runtime.pending) == 1
        assert int(dict(head.headers)["content-length"]) > 0
        asset = await application.http(request_from_headers("/demo/sample.bin", []))
        assert asset.body == b"\x00\xff"
        assert dict(asset.headers)["content-type"] == "application/octet-stream"
        fresh = await application.http(
            request_from_headers(
                "/demo/sample.bin", [("if-none-match", "W/" + dict(asset.headers)["etag"])]
            )
        )
        assert fresh.status == 304
        assert fresh.body == b""
        assert (await application.http(request_from_headers("/elsewhere", []))).status == 404
        assert (
            await application.http(request_from_headers("/demo/", []), method="POST")
        ).status == 405
        await application.close()
        await application.close()
        assert cleaned == [None, "alice"]
        assert (await application.http(request_from_headers("/demo/", []))).status == 503

    asyncio.run(exercise())


def test_host_config_shutdown_finishes_after_cleanup_and_hook_failure() -> None:
    async def exercise() -> None:
        hooks: list[str] = []

        def fail() -> None:
            raise ValueError("cleanup failed")

        def app() -> Fragment:
            on_cleanup(fail)

            return native.P("pending")

        application = Application(
            app,
            config=HostConfig(
                shutdown=(lambda: hooks.append("finished"), fail),
            ),
        )
        await application.http(request_from_headers("/", []))
        with pytest.raises(ExceptionGroup, match="application shutdown") as caught:
            await application.close()
        assert len(caught.value.exceptions) == 2
        assert application.runtime.pending == {}
        assert hooks == ["finished"]
        await application.close()

    asyncio.run(exercise())


def test_host_config_shutdown_waits_for_startup_and_disposes_racing_get() -> None:
    async def exercise() -> None:
        entering = asyncio.Event()
        release = asyncio.Event()
        order: list[str] = []

        async def startup() -> None:
            entering.set()
            await release.wait()
            order.append("start")

        def app() -> Fragment:
            on_cleanup(lambda: order.append("dispose"))

            return native.P("racing request")

        application = Application(
            app, config=HostConfig(startup=(startup,), shutdown=(lambda: order.append("stop"),))
        )
        request = asyncio.create_task(application.http(request_from_headers("/", [])))
        await asyncio.wait_for(entering.wait(), 5)
        closing = asyncio.create_task(application.close())
        await asyncio.sleep(0)
        assert order == []
        release.set()
        assert (await request).status == 200
        await closing
        assert order == ["start", "dispose", "stop"]
        assert application.runtime.pending == {}

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ({"path": "/literal%2F"}, "/literal%252F"),
        ({"path": "/slash/value", "raw_path": b"/slash%2Fvalue"}, "/slash%2Fvalue"),
        ({"path": "/next", "root_path": "/100% ready"}, "/100%25%20ready/next"),
    ],
)
def test_host_config_asgi_decoded_and_raw_paths(scope: Event, expected: str) -> None:
    async def exercise() -> None:
        application = Application(lambda: native.P(current_request().path))

        try:
            sent = await http(application.asgi(), "/", scope=scope)
            assert sent[0]["status"] == 200
            body = sent[1]["body"]
            assert isinstance(body, bytes)
            assert expected.encode() in body
        finally:
            await application.close()

    asyncio.run(exercise())


def test_host_config_asgi_mount_headers_failed_send_and_lifespan() -> None:
    async def exercise() -> None:
        hooks: list[str] = []
        cleaned: list[str] = []
        contexts: list[RequestContext] = []

        def app() -> Fragment:
            contexts.append(current_request())
            on_cleanup(lambda: cleaned.append(current_request().path))

            return native.P("SSR")

        application = Application(
            app,
            config=HostConfig(
                startup=(lambda: hooks.append("start"),), shutdown=(lambda: hooks.append("stop"),)
            ),
        )
        adapter = application.asgi()
        sent = await http(
            adapter,
            "/a é",
            scope={
                "root_path": "/mount",
                "query_string": b"q=1",
                "headers": [(b"cookie", b"one=1"), (b"cookie", b"two=2")],
                "state": {"pysx_principal": "trusted"},
            },
        )
        context = contexts[-1]
        assert context.path == "/mount/a%20%C3%A9?q=1"
        assert context.headers["cookie"] == "one=1; two=2"
        assert context.principal == "trusted"
        assert sent[0]["status"] == 200
        body = sent[1]["body"]
        assert isinstance(body, bytes)
        assert b"SSR" in body

        async def receive() -> Event:
            return {"type": "http.request"}

        async def fail(_: Event) -> None:
            raise OSError("client disappeared")

        with pytest.raises(OSError, match="disappeared"):
            await adapter({"type": "http", "path": "/failed"}, receive, fail)
        assert cleaned == ["/failed"]
        assert len(application.runtime.pending) == 1
        lifespan_events: list[Event] = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        events = iter(lifespan_events)

        async def lifespan_receive() -> Event:
            return next(events)

        async def send(event: Event) -> None:
            sent.append(event)

        await adapter({"type": "lifespan"}, lifespan_receive, send)
        assert hooks == ["start", "stop"]
        assert cleaned == ["/failed", "/mount/a%20%C3%A9?q=1"]
        assert sent[-1]["type"] == "lifespan.shutdown.complete"

    asyncio.run(exercise())


@pytest.mark.parametrize("finish", ["disconnect", "cancel", "shutdown"])
def test_host_config_asgi_connected_session_cleanup(finish: str) -> None:
    async def exercise() -> None:
        cleaned: list[str] = []

        def app() -> Fragment:
            on_cleanup(lambda: cleaned.append("closed"))

            return native.P("connected")

        application = Application(app)
        adapter = application.asgi()
        incoming: asyncio.Queue[Event] = asyncio.Queue()
        incoming.put_nowait({"type": "websocket.connect"})
        initialized = asyncio.Event()

        async def send(event: Event) -> None:
            if event["type"] == "websocket.send" and isinstance(event.get("text"), str):
                message: object = json.loads(str(event["text"]))
                assert isinstance(message, dict)

                if cast("dict[str, object]", message).get("t") == "init":
                    initialized.set()

        task = asyncio.create_task(
            adapter(
                {"type": "websocket", "path": "/ws", "asgi": {"spec_version": "2.5"}},
                incoming.get,
                send,
            )
        )
        await asyncio.wait_for(initialized.wait(), 2)

        if finish == "disconnect":
            incoming.put_nowait({"type": "websocket.disconnect"})
            await asyncio.wait_for(task, 2)
        elif finish == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await application.close()
            await asyncio.gather(task, return_exceptions=True)
        await application.close()
        assert cleaned == ["closed"]
        assert application.connections == {}
        assert application.runtime.pending == {}

    asyncio.run(exercise())


def test_nginx_proxy_serves_the_parent_and_the_mounted_application() -> None:
    """Real nginx, real TLS: the full path survives, so a mounted prefix still resolves."""
    import tempfile
    import urllib.request
    from pathlib import Path

    from nginx_proxy import application, disposable_tls_client, free_port, nginx

    port = free_port()

    with (
        tempfile.TemporaryDirectory() as directory,
        application(
            "examples.nginx:example",
            environment={"PYSX_ORIGIN": f"https://localhost:{port}"},
        ) as upstream,
        nginx(Path(directory) / "proxy with spaces", upstream, port=port) as proxied,
    ):
        context = disposable_tls_client()
        pages: dict[str, tuple[int, str, str]] = {}

        for path in ("/", "/reports"):
            request = urllib.request.Request(f"https://localhost:{proxied}{path}")

            with urllib.request.urlopen(request, context=context, timeout=60) as response:
                pages[path] = (
                    response.status,
                    response.read().decode(),
                    response.headers.get("Set-Cookie", ""),
                )
    assert pages["/"][0] == 200
    assert pages["/reports"][0] == 200
    assert "reverse proxy" in pages["/"][1].lower()
    assert "path prefix" in pages["/reports"][1].lower()

    for path in ("/", "/reports"):
        assert "Secure" in pages[path][2], f"{path} must set a secure cookie behind TLS"


def test_nginx_proxy_mounted_application_shares_host_configuration() -> None:
    """One factory decides origins, proxy trust and cookies for parent and child."""
    from examples.nginx import MOUNT, Deployment

    deployment = Deployment()
    parent, child = deployment.config, deployment.reports.config
    assert child.base_path == MOUNT
    assert child.secure_cookies is parent.secure_cookies is True
    assert child.security.origins == parent.security.origins
    assert child.security.trusted_proxies == parent.security.trusted_proxies
    assert child.recovery_namespace != parent.recovery_namespace, (
        "a mounted application keeps its own recovery identity"
    )


def test_nginx_proxy_mounted_application_owns_its_socket_path() -> None:
    """A mounted application is upgraded and validated at its own prefix, not the root's."""
    from examples.nginx import MOUNT, Deployment

    deployment = Deployment()
    assert deployment.accepts_websocket("/ws"), "the parent still serves its own socket"
    assert deployment.accepts_websocket(f"{MOUNT}/ws"), "the mounted prefix is upgraded too"
    assert not deployment.accepts_websocket("/elsewhere/ws")

    # A plain application is unchanged: only its own socket path upgrades.
    plain = Application(lambda: pysx(t"div: 'plain'"), config=HostConfig())
    assert plain.accepts_websocket("/ws")
    assert not plain.accepts_websocket(f"{MOUNT}/ws")
    assert deployment.reports.path("ws") == f"{MOUNT}/ws"


def test_request_cookies_parse_into_a_read_only_snapshot() -> None:
    """An application reads the cookies a request carried, including HttpOnly ones."""
    context = request_from_headers("/", [("cookie", "theme=dark"), ("cookie", "density=compact")])
    assert context.cookies["theme"] == "dark"
    assert context.cookies["density"] == "compact"
    assert "absent" not in context.cookies

    with pytest.raises(TypeError):
        cast("dict[str, str]", context.cookies)["theme"] = "light"


def cookie_values(result: HTTPResult) -> list[str]:
    return [value for name, value in result.headers if name == "set-cookie"]


def test_response_cookies_reach_the_document_response_on_both_paths() -> None:
    """A cookie set during a render is carried by the response that render produced."""
    from pysx import set_cookie
    from pysx.hosting.http import HTTPRuntime
    from pysx.hosting.request import RequestContext

    def app() -> Fragment:
        set_cookie("theme", "dark", max_age=600)

        return native.Div("page")

    async def exercise() -> None:
        runtime = HTTPRuntime(app)
        result = runtime.render(RequestContext(), "cookie-browser")
        emitted = cookie_values(result)
        assert emitted == ["theme=dark; Path=/; SameSite=Lax; Max-Age=600"]
        assert "Secure" not in emitted[0], "plain http must not claim Secure"
        runtime.close()

    asyncio.run(exercise())


def test_response_cookies_derive_secure_from_the_request_scheme() -> None:
    """Secure is not a global flag: it follows the scheme this request arrived on."""
    from pysx import set_cookie
    from pysx.hosting.http import HTTPRuntime
    from pysx.hosting.request import RequestContext

    def app() -> Fragment:
        set_cookie("theme", "dark")

        return native.Div("page")

    async def exercise() -> None:
        runtime = HTTPRuntime(app)
        result = runtime.render(RequestContext(scheme="https"), "cookie-browser")
        assert cookie_values(result) == ["theme=dark; Path=/; SameSite=Lax; Secure"]
        runtime.close()

    asyncio.run(exercise())


def test_response_cookies_survive_a_redirect_response() -> None:
    """A redirecting render still carries the cookies it set."""
    from pysx import set_cookie
    from pysx.hosting.http import HTTPRuntime
    from pysx.hosting.request import Document, RequestContext

    def app() -> Fragment:
        set_cookie("theme", "dark")

        return native.Div("page")

    async def exercise() -> None:
        runtime = HTTPRuntime(app)
        document = Document(redirect="/elsewhere")
        result = runtime.render(RequestContext(document=document), "cookie-browser")
        assert result.status == 303
        assert cookie_values(result) == ["theme=dark; Path=/; SameSite=Lax"]
        runtime.close()

    asyncio.run(exercise())


def test_delete_cookie_expires_one_path_without_touching_another() -> None:
    """Removal identity is name plus path, matching what the browser enforces."""
    from pysx import delete_cookie, set_cookie
    from pysx.hosting.http import HTTPRuntime
    from pysx.hosting.request import RequestContext

    def app() -> Fragment:
        set_cookie("theme", "dark", path="/reports")
        delete_cookie("theme")

        return native.Div("page")

    async def exercise() -> None:
        runtime = HTTPRuntime(app)
        emitted = cookie_values(runtime.render(RequestContext(), "cookie-browser"))
        assert emitted == [
            "theme=dark; Path=/reports; SameSite=Lax",
            "theme=; Path=/; SameSite=Lax; Max-Age=0",
        ]
        runtime.close()

    asyncio.run(exercise())


def test_setting_one_cookie_twice_keeps_the_last_write_for_that_path() -> None:
    from pysx import set_cookie
    from pysx.hosting.http import HTTPRuntime
    from pysx.hosting.request import RequestContext

    def app() -> Fragment:
        set_cookie("theme", "dark")
        set_cookie("theme", "light")

        return native.Div("page")

    async def exercise() -> None:
        runtime = HTTPRuntime(app)
        assert cookie_values(runtime.render(RequestContext(), "cookie-browser")) == [
            "theme=light; Path=/; SameSite=Lax"
        ]
        runtime.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: Cookie("pysx_browser", "stolen"), id="reserved_name"),
        pytest.param(lambda: Cookie("theme", "a\r\nSet-Cookie: injected=1"), id="header_injection"),
        pytest.param(lambda: Cookie("bad name", "dark"), id="invalid_name"),
        pytest.param(lambda: Cookie("theme", "dark", path="relative"), id="relative_path"),
        pytest.param(
            # A literal type already refuses this spelling; the runtime guard must too.
            lambda: Cookie("theme", "dark", samesite=cast("SameSite", "Nope")),
            id="invalid_samesite",
        ),
        pytest.param(
            lambda: Cookie("theme", "dark", samesite="None", secure=False), id="insecure_none"
        ),
        pytest.param(lambda: Cookie("theme", "dark", max_age=-5), id="negative_max_age"),
    ],
)
def test_response_cookies_refuse_unsafe_input(build: Callable[[], object]) -> None:
    """Header injection, reserved names and contradictory attributes fail at the call."""

    with pytest.raises(CookieError):
        build()


def test_http_only_response_cookie_is_marked_for_the_browser_boundary() -> None:
    """An HttpOnly cookie is expressible, and is the one a script cannot read back."""
    rendered = Cookie("session", "abc", http_only=True).header(secure_default=True)
    assert rendered == "session=abc; Path=/; SameSite=Lax; HttpOnly; Secure"
