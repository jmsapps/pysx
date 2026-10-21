"""Adoption turns, cancellation and ordered background failure delivery."""

import asyncio
import json
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

import pytest

from pysx import ErrorInfo, Fragment, native, signal
from pysx.hosting.http import HTTPRuntime
from pysx.hosting.request import RequestContext
from pysx.runtime.connection import serve_connection

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class FakeConnection:
    def __init__(self, path: str, incoming: list[str]) -> None:
        self.request = RequestContext(
            path=path, headers=MappingProxyType({"cookie": "pysx_browser=b1"})
        )
        self.incoming = asyncio.Queue[str]()

        for message in incoming:
            self.incoming.put_nowait(message)
        self.sent: list[dict[str, object]] = []
        self.closed: tuple[int, str] | None = None
        self.release = asyncio.Event()

    async def send(self, message: str) -> None:
        decoded: object = json.loads(message)
        assert isinstance(decoded, dict)
        self.sent.append(cast("dict[str, object]", decoded))

    async def recv(self) -> str:
        return await self.incoming.get()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)
        self.release.set()

    async def __aiter__(self) -> AsyncIterator[str]:
        while True:
            yield await self.incoming.get()

    def frames(self, kind: str) -> list[dict[str, object]]:
        return [message for message in self.sent if message.get("t") == kind]


def adopted_runtime(app: object) -> tuple[HTTPRuntime, str]:
    runtime = HTTPRuntime(app)  # type: ignore[arg-type]
    request = RequestContext(path="/", headers=MappingProxyType({"cookie": "pysx_browser=b1"}))
    result = runtime.render(request, "b1")
    found = re.search(rb'data-pysx-adoption="([\w-]+)"', result.body)
    assert found is not None

    return runtime, found[1].decode()


def test_adoption_edits_wait_for_a_busy_mutation_turn() -> None:
    async def exercise() -> None:
        text = signal("server")

        def app() -> Fragment:
            return native.Div(native.Input(bind_value=text))

        runtime, token = adopted_runtime(app)
        session = runtime.pending[token].session
        (binding,) = session.rendered.handlers
        holding = asyncio.Event()
        finish = asyncio.Event()

        async def hold() -> None:
            async with session.runtime.turn():
                holding.set()
                await finish.wait()

        busy = asyncio.create_task(hold())
        await asyncio.wait_for(holding.wait(), 2)
        connection = FakeConnection(
            f"/ws?adoption={token}&location=/",
            [json.dumps({"t": "adopt", "edits": [{"h": binding, "v": "browser", "rev": 1}]})],
        )
        pump = asyncio.create_task(serve_connection(runtime, connection))

        for _ in range(20):
            if connection.frames("adopted") or connection.closed:
                break
            await asyncio.sleep(0)
        assert connection.closed is None
        assert connection.frames("adopted") == []
        assert text() == "server"
        finish.set()
        await busy

        for _ in range(20):
            if connection.frames("adopted") or connection.closed:
                break
            await asyncio.sleep(0)
        assert connection.closed is None
        assert connection.frames("adopted")
        assert text() == "browser"
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

    asyncio.run(exercise())


def test_adoption_cancellation_propagates_and_disposes() -> None:
    async def exercise() -> None:
        def app() -> Fragment:
            return native.P("waiting")

        runtime, token = adopted_runtime(app)
        session = runtime.pending[token].session
        connection = FakeConnection(f"/ws?adoption={token}&location=/", [])
        pump = asyncio.create_task(serve_connection(runtime, connection))
        await asyncio.sleep(0)
        pump.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pump
        assert connection.closed is None
        assert session.closed

    asyncio.run(exercise())


def test_background_failures_all_reach_the_browser() -> None:
    async def exercise() -> None:
        def app() -> Fragment:
            return native.P("idle")

        runtime, token = adopted_runtime(app)
        session = runtime.pending[token].session
        connection = FakeConnection(
            f"/ws?adoption={token}&location=/", [json.dumps({"t": "adopt", "edits": []})]
        )
        pump = asyncio.create_task(serve_connection(runtime, connection))

        for _ in range(20):
            if connection.frames("adopted"):
                break
            await asyncio.sleep(0)
        session.rendered.failures.append(ErrorInfo(None, "async"))
        session.rendered.failures.append(ErrorInfo(None, "effect"))
        session.changed.set()

        for _ in range(20):
            if len(connection.frames("result")) >= 2:
                break
            await asyncio.sleep(0)
        results = connection.frames("result")
        assert [message["error"]["phase"] for message in results] == ["async", "effect"]  # type: ignore[index]
        assert session.rendered.failures == []
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

    asyncio.run(exercise())
