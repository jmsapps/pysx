"""Epoch-local sequence admission, repair and real protocol pump behavior."""

import asyncio
import json
import re
import time
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING

import pytest
from test_connection_pump import FakeConnection

from pysx import Application, Fragment, TransportLimits, each, native, on_cleanup, on_mount, signal
from pysx.connection import BoundedConnection, serve_connection
from pysx.http import HTTPRuntime
from pysx.request import RequestContext
from pysx.transport import TransportError, TransportState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


async def wait_for(check: Callable[[], bool]) -> None:
    async with asyncio.timeout(3):
        while not check():
            await asyncio.sleep(0.001)


def test_current_state_registry_rotation_expiry_eviction_and_cleanup() -> None:
    async def exercise() -> None:
        cleaned: list[int] = []

        def app() -> Fragment:
            on_cleanup(lambda: cleaned.append(1))

            return native.P("current")

        runtime = HTTPRuntime(app, limits=TransportLimits(graphs=2, detached=1))
        request = RequestContext(headers=MappingProxyType({"cookie": "pysx_browser=b1"}))
        first = runtime.create_reserved(request)
        token = runtime.attach(first, request)
        with pytest.raises(ValueError, match="resume handle"):
            runtime.resume(token, request, first.transport.epoch, 0)
        runtime.detach(first)
        with pytest.raises(ValueError, match="resume handle"):
            runtime.resume(token, RequestContext(principal="other"), first.transport.epoch, 0)
        claimed = runtime.resume(token, request, first.transport.epoch, 0)
        assert claimed is first
        with pytest.raises(ValueError, match="resume handle"):
            runtime.resume(token, request, first.transport.epoch, 0)
        rotated = runtime.attach(first, request)
        assert rotated != token
        runtime.detach(first)
        second = runtime.create_reserved(request)
        runtime.attach(second, request)
        runtime.detach(second)
        assert first.closed
        assert cleaned == [1]
        deadline = next(iter(runtime.detached.values())).deadline
        assert deadline is not None
        runtime.reap(deadline)
        assert second.closed
        assert cleaned == [1, 1]
        assert not runtime.graphs
        assert not runtime.resume_handles
        assert not runtime.detached
        runtime.close()
        assert cleaned == [1, 1]

    asyncio.run(exercise())


def test_current_state_detached_complete_snapshot_without_pending_growth() -> None:
    async def exercise() -> None:
        rows = signal([1, 2, 3])
        label = signal("seed")
        checked = signal(True)
        setups: list[int] = []

        def app() -> Fragment:
            setups.append(1)

            return native.Main(
                native.Input(bind_value=label),
                native.Input(type="checkbox", bind_checked=checked),
                each(
                    rows,
                    lambda key: native.P(label, id=f"row-{key}", title=label),
                    key=lambda key: key,
                ),
            )

        runtime = HTTPRuntime(app)
        connection = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        session = runtime.create_reserved(connection.request)
        token = runtime.attach(session, connection.request)
        runtime.detach(session)

        for value in range(100):
            current = f"live-{value}"
            await session.runtime.update(partial(label.set, current))
        await session.runtime.update(lambda: rows.set([3, 4, 1]))
        await session.runtime.update(lambda: checked.set(False))
        assert not session.pending
        assert not session.pressured
        hello = json.dumps(
            {
                "t": "hello",
                "version": 3,
                "resume": token,
                "epoch": session.transport.epoch,
                "state_rev": 0,
            }
        )
        resumed = FakeConnection("/ws?protocol=3&resume=1", [hello])
        pump = asyncio.create_task(serve_connection(runtime, resumed))
        await wait_for(lambda: bool(resumed.frames("repair")))
        repair = resumed.frames("repair")[0]
        markup = str(repair["html"])
        assert "live-99" in markup
        assert "row-2" not in markup
        assert markup.index("row-3") < markup.index("row-4") < markup.index("row-1")
        assert " checked" not in markup
        assert repair["resume"] != token
        assert setups == [1]
        assert not runtime.detached
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        runtime.close()
        assert not runtime.resume_handles

    asyncio.run(exercise())


class DisconnectConnection(FakeConnection):
    async def __aiter__(self) -> AsyncIterator[str]:
        while True:
            message = await self.incoming.get()

            if message == "disconnect":
                return
            yield message


@pytest.mark.parametrize(
    "hello", ["null", "{", '{"t":"hello","version":4}', '{"t":"hello","version":3,"edits":[{}]}']
)
def test_current_state_malformed_claim_does_not_allocate_or_consume(hello: str) -> None:
    async def exercise() -> None:
        runtime = HTTPRuntime(lambda: native.P("kept"))
        connection = FakeConnection("/ws?protocol=3&resume=1", [hello])
        session = runtime.create_reserved(connection.request)
        token = runtime.attach(session, connection.request)
        runtime.detach(session)
        await serve_connection(runtime, connection)
        assert connection.closed == (4004, "resume unavailable")
        assert runtime.detached[token].session is session
        assert len(runtime.graphs) == 1
        runtime.close()

    asyncio.run(exercise())


def test_current_state_failed_network_send_expires_graph() -> None:
    class FailedSend(FakeConnection):
        async def send(self, message: str) -> None:
            assert message

            raise OSError("peer lost during send")

    async def exercise() -> None:
        runtime = HTTPRuntime(lambda: native.P("expire"))
        connection = FailedSend("/ws?protocol=3", ['{"t":"hello","version":3}'])
        with pytest.raises(OSError, match="peer lost"):
            await serve_connection(runtime, connection)
        assert not runtime.graphs
        assert not runtime.detached
        assert not runtime.resume_handles

    asyncio.run(exercise())


def test_current_state_mutations_during_stalled_resume_publish_are_not_lost() -> None:
    class SlowResume(FakeConnection):
        def __init__(self, hello: str) -> None:
            super().__init__("/ws?protocol=3&resume=1", [hello])
            self.started = [asyncio.Event(), asyncio.Event()]
            self.gates = [asyncio.Event(), asyncio.Event()]

        async def send(self, message: str) -> None:
            index = len(self.sent)
            await super().send(message)

            if index < 2:
                self.started[index].set()
                await self.gates[index].wait()

    async def exercise() -> None:
        label = signal("seed")
        runtime = HTTPRuntime(lambda: native.P(label))
        request = FakeConnection("/", []).request
        session = runtime.create_reserved(request)
        token = runtime.attach(session, request)
        runtime.detach(session)
        connection = SlowResume(
            json.dumps(
                {
                    "t": "hello",
                    "version": 3,
                    "resume": token,
                    "epoch": session.transport.epoch,
                    "state_rev": 0,
                }
            )
        )
        pump = asyncio.create_task(serve_connection(runtime, connection))
        await connection.started[0].wait()
        await session.runtime.update(lambda: label.set("first"))
        connection.gates[0].set()
        await connection.started[1].wait()
        await session.runtime.update(lambda: label.set("second"))
        connection.gates[1].set()
        await wait_for(lambda: len(connection.frames("patch")) == 2)
        assert "second" in json.dumps(connection.frames("patch")[-1])
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        runtime.close()

    asyncio.run(exercise())


def test_current_state_actual_disconnect_resume_at_capacity_and_shutdown() -> None:
    async def exercise() -> None:
        cleaned: list[int] = []

        def app() -> Fragment:
            on_cleanup(lambda: cleaned.append(1))

            return native.P("retained")

        runtime = HTTPRuntime(app, limits=TransportLimits(graphs=1, detached=1))
        first = DisconnectConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        pump = asyncio.create_task(serve_connection(runtime, first))
        await wait_for(lambda: bool(first.frames("init")))
        initial = first.frames("init")[0]
        first.incoming.put_nowait("disconnect")
        await pump
        assert len(runtime.graphs) == 1
        assert len(runtime.detached) == 1
        assert cleaned == []
        assert not runtime.reserve()
        hello = json.dumps(
            {
                "t": "hello",
                "version": 3,
                "resume": initial["resume"],
                "epoch": initial["epoch"],
                "state_rev": initial["state_rev"],
            }
        )
        second = DisconnectConnection("/ws?protocol=3&resume=1", [hello])
        resumed = asyncio.create_task(serve_connection(runtime, second))
        await wait_for(lambda: bool(second.frames("repair")))
        assert second.frames("repair")[0]["epoch"] == initial["epoch"]
        duplicate = DisconnectConnection("/ws?protocol=3&resume=1", [hello])
        await serve_connection(runtime, duplicate)
        assert duplicate.closed == (4004, "resume unavailable")
        second.incoming.put_nowait("disconnect")
        await resumed
        runtime.close()
        assert cleaned == [1]
        assert not runtime.graphs
        assert not runtime.detached

    asyncio.run(exercise())


@pytest.mark.parametrize(("code", "retained"), [(1000, True), (4002, False), (1008, False)])
def test_current_state_peer_close_policy(code: int, *, retained: bool) -> None:
    class PeerClose(DisconnectConnection):
        close_code = code

    async def exercise() -> None:
        runtime = HTTPRuntime(lambda: native.P("policy"))
        connection = PeerClose("/ws?protocol=3", ['{"t":"hello","version":3}'])
        pump = asyncio.create_task(serve_connection(runtime, connection))
        await wait_for(lambda: bool(connection.frames("init")))
        connection.incoming.put_nowait("disconnect")
        await pump
        assert bool(runtime.detached) is retained
        runtime.close()

    asyncio.run(exercise())


def test_current_state_shutdown_awaits_work_started_during_socket_close() -> None:
    async def exercise() -> None:
        host = Application(lambda: native.P("owned"))
        entered = asyncio.Event()
        cleaned = asyncio.Event()
        request = FakeConnection("/", []).request
        session = host.runtime.create_reserved(request)

        async def late_resource() -> None:
            entered.set()

            try:
                await asyncio.sleep(60)
            finally:
                await asyncio.sleep(0.02)
                cleaned.set()

        class CloseSpawn(FakeConnection):
            async def close(self, code: int = 1000, reason: str = "") -> None:
                session.runtime.spawn(late_resource)
                await entered.wait()
                await super().close(code, reason)

        connection = CloseSpawn("/", [])
        task = asyncio.current_task()
        assert task is not None
        host.connections[id(connection)] = (connection, task)
        await host.close()
        assert cleaned.is_set()
        assert not host.runtime.tasks()

    asyncio.run(exercise())


def test_bounded_reconnect_slow_consumer_does_not_block_healthy_peer() -> None:
    async def exercise() -> None:
        runtime = HTTPRuntime(
            lambda: native.P("healthy"),
            limits=TransportLimits(send_timeout=0.02, close_timeout=0.02),
        )
        slow = StalledConnection()
        stalled = asyncio.create_task(serve_connection(runtime, slow))
        healthy = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        live = asyncio.create_task(serve_connection(runtime, healthy))
        await wait_for(lambda: bool(healthy.frames("init")))
        outcomes = await asyncio.gather(stalled, return_exceptions=True)
        assert isinstance(outcomes[0], TransportError)
        assert slow.aborted
        assert not live.done()
        assert len(runtime.graphs) == 1
        live.cancel()
        await asyncio.gather(live, return_exceptions=True)
        runtime.close()
        assert not runtime.tasks()

    asyncio.run(exercise())


def test_bounded_reconnect_expired_claim_requires_fresh_epoch_without_replay() -> None:
    async def exercise() -> None:
        setups: list[int] = []

        def app() -> Fragment:
            setups.append(1)

            return native.P("fresh")

        runtime = HTTPRuntime(app)
        first = DisconnectConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        pump = asyncio.create_task(serve_connection(runtime, first))
        await wait_for(lambda: bool(first.frames("init")))
        initial = first.frames("init")[0]
        first.incoming.put_nowait("disconnect")
        await pump
        handle = next(iter(runtime.detached.values()))
        assert handle.deadline is not None
        runtime.reap(handle.deadline)
        stale = FakeConnection(
            "/ws?protocol=3&resume=1",
            [
                json.dumps(
                    {
                        "t": "hello",
                        "version": 3,
                        "resume": initial["resume"],
                        "epoch": initial["epoch"],
                        "state_rev": initial["state_rev"],
                    }
                )
            ],
        )
        await serve_connection(runtime, stale)
        assert stale.closed == (4004, "resume unavailable")
        assert setups == [1]
        fresh = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        live = asyncio.create_task(serve_connection(runtime, fresh))
        await wait_for(lambda: bool(fresh.frames("init")))
        assert fresh.frames("init")[0]["epoch"] != initial["epoch"]
        assert setups == [1, 1]
        live.cancel()
        await asyncio.gather(live, return_exceptions=True)
        runtime.close()

    asyncio.run(exercise())


def test_shape_protocol_high_water_survives_ack_eviction() -> None:
    state = TransportState("<p>original</p>")

    for sequence in range(1, 100):
        message: dict[str, object] = {"epoch": state.epoch, "state_rev": 0, "seq": sequence}
        assert state.accept(message) == "accepted"
        state.acknowledge(sequence, "error" if sequence == 1 else "success")
    assert 1 not in state.acknowledgements
    assert state.accept({"epoch": state.epoch, "state_rev": 0, "seq": 1}) == "duplicate"
    assert state.accept({"epoch": state.epoch, "state_rev": 0, "seq": 101}) == "gap"
    assert state.accept({"epoch": "wrong", "state_rev": 0, "seq": 100}) == "epoch"
    assert state.accept({"epoch": state.epoch, "state_rev": 999, "seq": 100}) == "revision"
    assert state.sequence == 99


def test_shape_protocol_lost_ack_gap_and_current_repair() -> None:
    async def exercise() -> None:
        count = signal(0)

        def app() -> Fragment:
            return native.Div(
                native.Button("increment", on_click=lambda _event: count.set(count() + 1)),
                native.P(count),
            )

        runtime = HTTPRuntime(app)
        connection = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3,"edits":[]}'])
        pump = asyncio.create_task(serve_connection(runtime, connection))
        await wait_for(lambda: bool(connection.frames("init")))
        initial = connection.frames("init")[0]
        found = re.search(r'data-pysx-click="([^"]+)"', str(initial["html"]))
        assert found is not None

        def event(sequence: int, **changes: object) -> str:
            return json.dumps(
                {
                    "t": "event",
                    "h": found[1],
                    "version": 3,
                    "epoch": initial["epoch"],
                    "state_rev": 1,
                    "seq": sequence,
                    **changes,
                }
            )

        connection.incoming.put_nowait(event(1))
        await wait_for(lambda: bool(connection.frames("ack")))
        assert count() == 1
        connection.incoming.put_nowait(event(1))
        await wait_for(lambda: len(connection.frames("ack")) == 2)
        assert count() == 1
        assert connection.frames("ack")[-1]["status"] == "duplicate"
        connection.incoming.put_nowait(event(3))
        await wait_for(lambda: bool(connection.frames("repair")))
        assert count() == 1
        assert ">1<" in str(connection.frames("repair")[-1]["html"])
        connection.incoming.put_nowait(event(2, h="unknown"))
        await wait_for(lambda: bool(connection.frames("result")))
        assert connection.frames("result")[-1]["status"] == "stale"
        connection.incoming.put_nowait(event(2))
        await wait_for(lambda: len(connection.frames("ack")) == 4)
        assert count() == 1

        def acknowledged(sequence: int) -> Callable[[], bool]:
            return lambda: connection.frames("ack")[-1]["seq"] == sequence

        for sequence in range(3, 71):
            connection.incoming.put_nowait(event(sequence))
            await wait_for(acknowledged(sequence))
        assert count() == 69
        connection.incoming.put_nowait(event(1))
        await wait_for(lambda: connection.frames("ack")[-1]["seq"] == 1)
        assert connection.frames("ack")[-1]["status"] == "duplicate"
        assert count() == 69
        revisions = [frame["state_rev"] for frame in connection.sent]
        assert revisions == list(range(1, len(revisions) + 1))
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        runtime.close()

    asyncio.run(exercise())


class StalledConnection(FakeConnection):
    def __init__(self) -> None:
        super().__init__("/ws?protocol=3", ['{"t":"hello","version":3}'])
        self.aborted = False
        self.order: list[str] = []
        self.gate = asyncio.Event()

    def abort(self) -> None:
        self.aborted = True
        self.order.append("abort")
        self.gate.set()

    async def send(self, message: str) -> None:
        assert isinstance(message, str)

        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.order.append("cancel send")
            assert self.aborted

            raise

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)
        self.order.append("close")
        await self.gate.wait()


def test_bound_sends_stalled_io_closes_then_aborts_before_cancellation() -> None:
    async def exercise() -> None:
        raw = StalledConnection()
        bounded = BoundedConnection(raw, TransportLimits(send_timeout=0.02, close_timeout=0.02))
        started = time.monotonic()
        with pytest.raises(TransportError, match="send deadline"):
            await bounded.send("bounded")
        assert time.monotonic() - started < 0.5
        assert raw.aborted
        assert raw.order[:2] == ["close", "abort"]
        closing = StalledConnection()
        await BoundedConnection(closing, bounded.limits).close()
        assert closing.aborted

    asyncio.run(exercise())


def test_bound_sends_failed_sender_supervises_workers_and_releases_graph() -> None:
    async def exercise() -> None:
        runtime = HTTPRuntime(
            lambda: native.P("live"), limits=TransportLimits(send_timeout=0.02, close_timeout=0.02)
        )
        raw = StalledConnection()
        with pytest.raises(TransportError, match="send deadline"):
            await asyncio.wait_for(serve_connection(runtime, raw), 0.5)
        assert raw.aborted
        assert runtime.graphs == set()
        assert runtime.reservations == 0
        assert not any(not task.done() for task in runtime.tasks())

    asyncio.run(exercise())


def test_bound_sends_hello_timeout_releases_admission() -> None:
    async def exercise() -> None:
        runtime = HTTPRuntime(
            lambda: native.P("unused"), limits=TransportLimits(hello_timeout=0.02)
        )
        connection = FakeConnection("/ws?protocol=3", [])
        await asyncio.wait_for(serve_connection(runtime, connection), 0.5)
        assert connection.closed == (1008, "invalid hello")
        assert runtime.reservations == 0
        assert runtime.graphs == set()

    asyncio.run(exercise())


def test_bound_sends_inbound_frame_limit_closes_and_releases_graph() -> None:
    async def exercise() -> None:
        runtime = HTTPRuntime(lambda: native.P("live"), limits=TransportLimits(max_frame_bytes=128))
        connection = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        pump = asyncio.create_task(serve_connection(runtime, connection))
        await wait_for(lambda: bool(connection.frames("init")))
        connection.incoming.put_nowait("x" * 129)
        with pytest.raises(TransportError, match="inbound"):
            await asyncio.wait_for(pump, 0.5)
        assert connection.closed == (1009, "inbound frame limit")
        assert runtime.graphs == set()

    asyncio.run(exercise())


def test_bound_sends_outbound_batch_and_outbox_pressure_expire_graphs() -> None:
    async def exercise() -> None:
        small = HTTPRuntime(
            lambda: native.P("x" * 1024), limits=TransportLimits(max_batch_bytes=512)
        )
        connection = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        with pytest.raises(TransportError, match="batch"):
            await serve_connection(small, connection)
        assert connection.closed == (1009, "outbound batch limit")
        assert small.graphs == set()
        value = signal("initial")
        runtime = HTTPRuntime(lambda: native.P(value))
        active = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        pump = asyncio.create_task(serve_connection(runtime, active))
        await wait_for(lambda: bool(active.frames("init")))
        (session,) = runtime.graphs
        await session.runtime.update(lambda: value.set("x" * (runtime.limits.outbox_bytes + 1)))
        with pytest.raises(TransportError, match="outbox"):
            await asyncio.wait_for(pump, 0.5)
        assert active.closed == (1013, "session outbox limit")
        assert session.closed
        assert runtime.graphs == set()

    asyncio.run(exercise())


def test_shape_protocol_failed_handler_ack_is_not_replayed() -> None:
    async def exercise() -> None:
        calls: list[object] = []

        def fail(event: object) -> None:
            calls.append(event)

            raise ValueError("application failure")

        runtime = HTTPRuntime(lambda: native.Button("fail", on_click=fail))
        connection = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        pump = asyncio.create_task(serve_connection(runtime, connection))
        await wait_for(lambda: bool(connection.frames("init")))
        initial = connection.frames("init")[0]
        found = re.search(r'data-pysx-click="([^"]+)"', str(initial["html"]))
        assert found is not None
        payload = json.dumps(
            {
                "t": "event",
                "h": found[1],
                "version": 3,
                "epoch": initial["epoch"],
                "state_rev": 1,
                "seq": 1,
            }
        )
        connection.incoming.put_nowait(payload)
        await wait_for(lambda: bool(connection.frames("result")))
        assert connection.frames("result")[0]["status"] == "error"
        assert connection.frames("result")[0]["seq"] == 1
        connection.incoming.put_nowait(payload)
        await wait_for(lambda: bool(connection.frames("ack")))
        assert connection.frames("ack")[0]["status"] == "duplicate"
        assert len(calls) == 1
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

    asyncio.run(exercise())


def test_shape_protocol_transaction_ownership_precedes_side_effects() -> None:
    async def exercise() -> None:
        calls: list[object] = []
        value = signal("initial")
        runtime = HTTPRuntime(
            lambda: native.Div(
                native.Input(bind_value=value), native.Button("call", on_click=calls.append)
            )
        )
        connection = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        pump = asyncio.create_task(serve_connection(runtime, connection))
        await wait_for(lambda: bool(connection.frames("init")))
        initial = connection.frames("init")[0]
        handler = re.search(r'data-pysx-click="([^"]+)"', str(initial["html"]))
        binding = re.search(r'data-pysx-binding="([^"]+)"', str(initial["html"]))
        assert handler is not None
        assert binding is not None

        payloads: list[dict[str, object]] = [
            {
                "edits": [
                    {"h": binding[1], "v": "changed", "rev": 1},
                    {"h": "unknown", "v": "bad", "rev": 1},
                ]
            },
            {"after": "unknown"},
        ]

        for sequence, payload in enumerate(payloads, start=1):
            connection.incoming.put_nowait(
                json.dumps(
                    {
                        "t": "event",
                        "h": handler[1],
                        "version": 3,
                        "epoch": initial["epoch"],
                        "state_rev": 1,
                        "seq": sequence,
                        **payload,
                    }
                )
            )
            await wait_for(lambda: len(connection.frames("result")) == len(calls) + 1)
            assert connection.frames("result")[-1]["status"] == "invalid"
            assert calls == []
            assert value() == "initial"
            connection.sent.clear()
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

    asyncio.run(exercise())


def test_repair_reannounces_unacknowledged_mounts() -> None:
    async def exercise() -> None:
        mounted: list[int] = []

        def body() -> Fragment:
            on_mount(lambda: mounted.append(1))

            return native.P("scoped")

        runtime = HTTPRuntime(lambda: native.Div(body()))
        connection = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        pump = asyncio.create_task(serve_connection(runtime, connection))
        await wait_for(lambda: bool(connection.frames("mount")))
        tokens = connection.frames("mount")[0]["ids"]
        assert tokens
        initial = connection.frames("init")[0]
        connection.sent.clear()
        connection.incoming.put_nowait(
            json.dumps({"t": "repair", "version": 3, "epoch": initial["epoch"], "state_rev": 1})
        )
        await wait_for(lambda: bool(connection.frames("mount")))
        assert connection.frames("repair")
        assert connection.frames("mount")[0]["ids"] == tokens
        assert mounted == []
        connection.incoming.put_nowait(
            json.dumps(
                {
                    "t": "mounted",
                    "ids": tokens,
                    "version": 3,
                    "epoch": initial["epoch"],
                    "state_rev": 1,
                    "seq": 1,
                }
            )
        )
        await wait_for(lambda: mounted == [1])
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

    asyncio.run(exercise())


def test_stale_chained_handler_still_commits_an_owned_binding_edit() -> None:
    async def exercise() -> None:
        value = signal("initial")
        runtime = HTTPRuntime(lambda: native.Div(native.Input(bind_value=value)))
        connection = FakeConnection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        pump = asyncio.create_task(serve_connection(runtime, connection))
        await wait_for(lambda: bool(connection.frames("init")))
        initial = connection.frames("init")[0]
        binding = re.search(r'data-pysx-binding="([^"]+)"', str(initial["html"]))
        assert binding is not None
        connection.sent.clear()
        connection.incoming.put_nowait(
            json.dumps(
                {
                    "t": "event",
                    "h": binding[1],
                    "v": "typed",
                    "rev": 1,
                    "after": "removed-by-rerender",
                    "version": 3,
                    "epoch": initial["epoch"],
                    "state_rev": 1,
                    "seq": 1,
                }
            )
        )
        await wait_for(lambda: bool(connection.frames("ack")))
        assert connection.frames("ack")[0]["status"] == "success"
        assert not connection.frames("result")
        assert value() == "typed"
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

    asyncio.run(exercise())
