"""Shared host contracts and ASGI lifecycle/disconnect boundaries."""

import asyncio
import json
from typing import TYPE_CHECKING, cast

import pytest

from pysx import Application, Fragment, HostConfig, StaticAsset, current_request, native, on_cleanup
from pysx.hosting.hosting import request_from_headers

if TYPE_CHECKING:
    from pysx.hosting.asgi import ASGI, Event
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
        await entering.wait()
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
