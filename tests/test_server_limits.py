"""Shared admission, finite shutdown and real process signal qualification."""

import asyncio
import json
import os
import re
import signal as signals
import socket
import sys
import time
from typing import TYPE_CHECKING
from urllib.request import urlopen

import pytest
from acceptance_support import ready_server
from test_connection_pump import FakeConnection
from test_transport import wait_for
from uvicorn import Config
from uvicorn.server import ServerState
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from pysx import Application, Fragment, HostConfig, TransportLimits, native, on_cleanup, resource
from pysx.hosting._uvicorn import websocket_protocol
from pysx.hosting.hosting import request_from_headers

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pysx.runtime.wire import JSONValue


async def http_method_parser(port: int) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    try:
        writer.write(b"HE")
        await writer.drain()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(reader.read(1), 0.03)
        writer.write(b"AD / HTTP/1.1\r\n")
        await writer.drain()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(reader.read(1), 0.03)
        writer.write(b"Host: localhost\r\nConnection: close\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 2)
        headers, separator, body = response.partition(b"\r\n\r\n")
        assert separator
        assert headers.startswith(b"HTTP/1.1 200")
        assert body == b""
    finally:
        writer.close()
        await writer.wait_closed()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    try:
        writer.write(b"X" * 8192)
        await writer.drain()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(reader.read(1), 0.03)
        writer.write(b"X")
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 2) == b""
    finally:
        writer.close()
        await writer.wait_closed()


def test_standalone_http_method_parser() -> None:
    async def exercise() -> None:
        application = Application(lambda: native.P("parser"))
        async with application.listen(port=0) as server:
            assert server.sockets is not None
            await http_method_parser(server.sockets[0].getsockname()[1])

    asyncio.run(exercise())


def build_config(factory: Callable[..., object], field: str, value: float) -> object:
    """Exercise runtime validation with deliberately invalid field values."""

    return factory(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("graphs", 0),
        ("graphs", True),
        ("hello_timeout", float("nan")),
        ("send_timeout", 0),
        ("close_timeout", float("inf")),
        ("max_frame_bytes", -1),
        ("event_queue", 1.5),
        ("socket_queue", False),
        ("resume_ttl", -1),
        ("write_limit", 0),
        ("detached", 101),
        ("max_batch_bytes", 3000000),
    ],
)
def test_bound_sends_malformed_limits(field: str, value: float) -> None:
    with pytest.raises(ValueError, match=r"transport|limits"):
        build_config(TransportLimits, field, value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("render_capacity", True),
        ("render_ttl", float("nan")),
        ("shutdown_timeout", float("inf")),
        ("task_capacity", 0),
    ],
)
def test_bound_sends_malformed_host_config(field: str, value: float) -> None:
    with pytest.raises(ValueError, match="must be"):
        build_config(HostConfig, field, value)


def test_bound_sends_shared_admission_hello_cancellation_and_shutdown() -> None:
    async def exercise() -> None:
        cleaned: list[bool] = []

        def app() -> Fragment:
            on_cleanup(lambda: cleaned.append(True))

            async def load() -> str:
                await asyncio.sleep(60)

                return "done"

            resource("waiting", load)

            return native.P("live")

        application = Application(
            app, config=HostConfig(transport=TransportLimits(graphs=2, detached=1))
        )
        page = await application.http(request_from_headers("/", []))
        assert page.status == 200
        first = FakeConnection("/ws?protocol=3", [])
        opening = asyncio.create_task(application.websocket(first))
        await wait_for(lambda: application.runtime.reservations == 1)
        refused = FakeConnection("/ws?protocol=3", [])
        await application.websocket(refused)
        assert refused.closed == (1013, "graph capacity")
        assert len(application.runtime.graphs) == 1
        assert (await application.http(request_from_headers("/", []))).status == 503
        assert (await application.http(request_from_headers("/client.js", []))).status == 200
        assert (await application.http(request_from_headers("/health", []))).body == b"ok"
        opening.cancel()
        await asyncio.gather(opening, return_exceptions=True)
        assert application.runtime.reservations == 0
        active = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        pump = asyncio.create_task(application.websocket(active))
        await wait_for(lambda: bool(active.frames("init")))
        assert len(application.runtime.graphs) == 2
        tasks = application.runtime.tasks()
        assert tasks
        await application.close()
        await asyncio.gather(pump, return_exceptions=True)
        assert all(task.done() for task in tasks)
        assert application.runtime.graphs == set()
        assert application.runtime.pending == {}
        assert application.connections == {}
        assert application.runtime.reservations == 0
        assert len(cleaned) == 2

    asyncio.run(exercise())


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))

        return int(sock.getsockname()[1])


def test_bound_sends_simultaneous_handshakes_never_overshoot() -> None:
    async def exercise() -> None:
        application = Application(
            lambda: native.P("live"),
            config=HostConfig(transport=TransportLimits(graphs=2, detached=1)),
        )
        connections = [FakeConnection("/ws?protocol=3", []) for _ in range(8)]
        pumps = [
            asyncio.create_task(application.websocket(connection)) for connection in connections
        ]
        await wait_for(
            lambda: sum(connection.closed is not None for connection in connections) == 6
        )
        assert application.runtime.reservations == 2
        assert len(application.connections) == 2
        assert application.runtime.graphs == set()

        for connection in connections:
            if connection.closed is None:
                connection.incoming.put_nowait('{"t":"hello","version":3}')
        await wait_for(
            lambda: sum(bool(connection.frames("init")) for connection in connections) == 2
        )
        assert len(application.runtime.graphs) == 2
        assert application.runtime.reservations == 0
        await application.close()
        await asyncio.gather(*pumps, return_exceptions=True)
        assert application.runtime.graphs == set()
        assert application.connections == {}

    asyncio.run(exercise())


def test_bound_sends_shutdown_releases_sockets_waiting_for_hello() -> None:
    async def exercise() -> None:
        application = Application(lambda: native.P("unused"))
        connection = FakeConnection("/ws?protocol=3", [])
        pump = asyncio.create_task(application.websocket(connection))
        await wait_for(lambda: application.runtime.reservations == 1)
        await application.close()
        await asyncio.gather(pump, return_exceptions=True)
        assert application.runtime.reservations == 0
        assert application.connections == {}
        assert application.handshakes == {}
        assert application.runtime.graphs == set()

    asyncio.run(exercise())


def test_bound_sends_real_upgrade_reservations_and_adoption_at_capacity() -> None:
    async def exercise() -> None:
        application = Application(
            lambda: native.P("live"),
            config=HostConfig(transport=TransportLimits(graphs=2, detached=1)),
        )
        async with application.listen(port=0) as server:
            assert server.sockets is not None
            port = server.sockets[0].getsockname()[1]
            address = f"ws://127.0.0.1:{port}/ws?protocol=3"
            first = await connect(address)
            second = await connect(address)
            await wait_for(lambda: application.runtime.reservations == 2)
            with pytest.raises(InvalidStatus) as caught:
                await connect(address)
            assert caught.value.response.status_code == 503

            def get(path: str) -> tuple[int, bytes, str]:
                with urlopen(f"http://127.0.0.1:{port}/{path}", timeout=3) as response:
                    return response.status, response.read(), response.headers.get("set-cookie", "")

            assert (await asyncio.to_thread(get, "client.js"))[0] == 200
            assert (await asyncio.to_thread(get, "health"))[1] == b"ok"
            await first.close()
            await wait_for(lambda: application.runtime.reservations == 1)
            status, body, cookie = await asyncio.to_thread(get, "")
            assert status == 200
            found = re.search(rb'data-pysx-adoption="([\w-]+)"', body)
            assert found is not None
            async with connect(
                address + "&adoption=" + found[1].decode(),
                additional_headers={"Cookie": cookie.split(";")[0]},
            ) as adopted:
                await adopted.send('{"t":"hello","version":3,"edits":[]}')
                assert json.loads(await adopted.recv())["t"] == "adopted"
                assert len(application.runtime.graphs) == 1
                assert application.runtime.reservations == 1
            await second.close()
        assert application.runtime.reservations == 0
        assert application.runtime.graphs == set()
        assert application.connections == {}

    asyncio.run(exercise())


def test_bound_sends_shutdown_cancels_stalled_startup() -> None:
    async def exercise() -> None:
        started = asyncio.Event()

        async def startup() -> None:
            started.set()
            await asyncio.Event().wait()

        application = Application(
            lambda: native.P("unused"), config=HostConfig(startup=(startup,), shutdown_timeout=0.05)
        )
        request = asyncio.create_task(application.http(request_from_headers("/", [])))
        await started.wait()
        with pytest.raises(TimeoutError, match="shutdown deadline"):
            await application.close()
        await asyncio.gather(request, return_exceptions=True)
        assert request.done()
        assert application.start_tasks == set()
        assert application.runtime.graphs == set()
        assert application.closed

    asyncio.run(exercise())


def test_bound_sends_idle_tcp_handshake_cannot_outlive_shutdown_grace() -> None:
    async def exercise() -> None:
        application = Application(
            lambda: native.P("unused"),
            config=HostConfig(shutdown_timeout=0.1, transport=TransportLimits(hello_timeout=0.8)),
        )
        started = 0.0
        writer: asyncio.StreamWriter | None = None
        async with application.listen(port=0) as server:
            assert server.sockets is not None
            port = server.sockets[0].getsockname()[1]
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await asyncio.sleep(0.01)
            started = time.monotonic()
        assert time.monotonic() - started < 0.3
        assert application.runtime.graphs == set()
        assert application.handshakes == {}
        assert writer is not None
        writer.close()
        await writer.wait_closed()

    asyncio.run(exercise())


def test_bound_sends_stalled_asgi_adoption_accepts_share_handshake_admission() -> None:
    async def exercise() -> None:
        application = Application(
            lambda: native.P("unused"),
            config=HostConfig(transport=TransportLimits(graphs=2, detached=1, close_timeout=0.02)),
        )
        accepts = 0
        aborted: list[bool] = []
        scopes: list[asyncio.Task[None]] = []

        async def receive() -> dict[str, object]:
            return {"type": "websocket.connect"}

        async def send(message: dict[str, object]) -> None:
            nonlocal accepts

            if message["type"] == "websocket.accept":
                accepts += 1
                await asyncio.Event().wait()

        for _ in range(6):
            scopes.append(
                asyncio.create_task(
                    application.asgi()(
                        {
                            "type": "websocket",
                            "path": "/ws",
                            "headers": [],
                            "query_string": b"adoption=unclaimed",
                            "extensions": {
                                "pysx.transport": {"abort": lambda: aborted.append(True)}
                            },
                        },
                        receive,
                        send,
                    )
                )
            )
        await wait_for(
            lambda: len(application.handshakes) == 2 and sum(task.done() for task in scopes) == 4
        )
        assert accepts == 2
        assert application.runtime.reservations == 0
        await application.close()
        await asyncio.gather(*scopes, return_exceptions=True)
        assert all(task.done() for task in scopes)
        assert application.handshakes == {}
        assert application.connections == {}
        assert application.runtime.graphs == set()
        assert aborted

    asyncio.run(exercise())


def test_bound_sends_asgi_close_aborts_when_protocol_receive_queue_is_full() -> None:
    class Transport(asyncio.Transport):
        def __init__(self) -> None:
            self.aborted = False

        def get_extra_info(self, name: str, default: object = None) -> object:
            return ("127.0.0.1", 10000) if name in ("peername", "sockname") else default

        def set_write_buffer_limits(self, high: int | None = None, low: int | None = None) -> None:
            assert high == 32768
            assert low is None

        def is_closing(self) -> bool:
            return self.aborted

        def abort(self) -> None:
            self.aborted = True

    async def exercise() -> None:
        application = Application(lambda: native.P("unused"))
        protocol_type = websocket_protocol(TransportLimits(socket_queue=1))
        protocol = protocol_type(Config(application.asgi(), log_config=None), ServerState(), {})
        transport = Transport()
        protocol.connection_made(transport)
        protocol.handshake_complete = True
        protocol.queue.put_nowait({"type": "websocket.receive", "text": "unadmitted"})
        await protocol.send({"type": "websocket.close", "code": 1001})
        assert transport.aborted
        assert protocol.close_timer is None
        await application.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("asgi", [False, True], ids=["standalone", "asgi"])
@pytest.mark.parametrize("name", [signals.SIGINT, signals.SIGTERM])
def test_bound_sends_real_signal_shutdown(
    asgi: bool, name: signals.Signals, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "shutdown.json"
    monkeypatch.setenv("PYSX_SHUTDOWN_REPORT", str(report))
    port = free_port()
    command = [
        sys.executable,
        "-m",
        "pysx.hosting.server",
        "--app",
        "tests.transport_limits_fixture:host",
        "--port",
        str(port),
    ]

    if asgi:
        command.append("--asgi")
    with ready_server(command, ready_banner="Uvicorn running" if asgi else "pysx ready") as process:
        with urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
            assert response.status == 200

        async def exercise() -> None:
            async with connect(f"ws://127.0.0.1:{port}/ws") as connection:
                assert json.loads(await connection.recv())["t"] == "init"
                started = time.monotonic()
                os.kill(process.pid, name)
                await asyncio.to_thread(process.wait, 5)
                assert time.monotonic() - started < 5

        asyncio.run(exercise())
        assert process.returncode == (-int(name) if asgi and name == signals.SIGTERM else 0)
    data: dict[str, JSONValue] = json.loads(report.read_text())
    assert data == {"graphs": 0, "tasks": 0, "connections": 0, "created": 2, "cleaned": 2}


def test_health_probe_path_relocates_and_disables_without_shadowing_routes() -> None:
    async def exercise() -> None:
        served = Application(lambda: native.P("status page"))

        assert (await served.http(request_from_headers("/health", []))).body == b"ok"

        moved = Application(
            lambda: native.P("status page"), config=HostConfig(health_path="/_probe/")
        )

        assert moved.config.health_path == "/_probe"
        assert (await moved.http(request_from_headers("/_probe", []))).body == b"ok"
        rendered = await moved.http(request_from_headers("/health", []))
        assert rendered.status == 200
        assert b"status page" in rendered.body

        disabled = Application(lambda: native.P("status page"), config=HostConfig(health_path=None))
        rendered = await disabled.http(request_from_headers("/health", []))
        assert rendered.status == 200
        assert b"status page" in rendered.body

        prefixed = Application(lambda: native.P("status page"), config=HostConfig(base_path="/app"))
        assert (await prefixed.http(request_from_headers("/app/health", []))).body == b"ok"

        for invalid in ("health", "/", ""):
            with pytest.raises(ValueError, match="health_path"):
                HostConfig(health_path=invalid)
        await asyncio.gather(served.close(), moved.close(), disabled.close(), prefixed.close())

    asyncio.run(exercise())
