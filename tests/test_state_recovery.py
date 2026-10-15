"""Declared state survives fresh graphs; uncommitted output never escapes."""

import asyncio
import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from typing import TYPE_CHECKING, cast

import pytest
from test_connection_pump import FakeConnection
from test_transport import wait_for

from pysx import (
    DurableSession,
    Fragment,
    IncompatibleState,
    MemoryStateStore,
    RecoveryUnavailable,
    Signal,
    SQLiteStateStore,
    StateCodec,
    StateIdentity,
    StateSnapshot,
    StoreUnavailable,
    native,
    on_cleanup,
    on_setup,
    resource,
)
from pysx.connection import serve_connection
from pysx.http import HTTPRuntime
from pysx.persistence import StateStore, migrate_state
from pysx.session import Session
from pysx.wire import JSONValue

if TYPE_CHECKING:
    from pathlib import Path

    from pysx.request import RequestContext


def integer(value: JSONValue) -> int:
    if type(value) is not int:
        raise ValueError("expected integer state")

    return value


CODEC = StateCodec[int](lambda value: value, integer)
IDENTITY = StateIdentity("counter", "session", "principal", "browser")


def test_explicit_state_names_volatile_and_durable_modes() -> None:
    assert Session.durability == "volatile"
    assert DurableSession.durability == "durable"


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> StateStore:
    return (
        MemoryStateStore() if request.param == "memory" else SQLiteStateStore(tmp_path / "state.db")
    )


def view(state: Signal[int]) -> Fragment:
    def increment(_value: object) -> None:
        state.set(state() + 1)

    return native.Div(
        native.Span(state),
        native.Button("increment", on_click=increment),
    )


def session(store: StateStore) -> DurableSession[int]:
    return DurableSession(view, initial=lambda: 0, codec=CODEC, store=store, identity=IDENTITY)


@pytest.mark.parametrize("value", [None, True, False, 0, -3, 1.5, "é", [], {}, [1, {"a": None}]])
def test_explicit_state_json_roundtrip(value: JSONValue) -> None:
    codec = StateCodec[JSONValue](lambda item: item, lambda item: item)
    assert codec.loads(codec.dumps(value)) == value


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), 2**53, b"bytes", (1,), {1: 2}, object()]
)
def test_explicit_state_rejects_unsupported_values(value: object) -> None:
    codec = StateCodec[object](lambda item: cast("JSONValue", item), lambda item: item)
    with pytest.raises(ValueError, match="state"):
        codec.dumps(value)


def test_explicit_state_bounds_and_malformed_input() -> None:
    codec = StateCodec[JSONValue](lambda item: item, lambda item: item, max_bytes=64)

    for payload in (b"x", b'"' + b"x" * 100 + b'"', b"NaN", b"[" * 200 + b"]" * 200):
        with pytest.raises(ValueError, match=r"Expecting|state"):
            codec.loads(payload)

    for value in (cast("JSONValue", [None] * 4097), "x" * 100):
        with pytest.raises(ValueError, match="state"):
            codec.dumps(value)
    nested: JSONValue = None

    for _ in range(18):
        nested = [nested]
    with pytest.raises(ValueError, match="nesting"):
        codec.dumps(nested)


def test_explicit_state_migration_and_incompatible_version(store: StateStore) -> None:
    original = StateSnapshot(IDENTITY, 1, b"4")
    assert migrate_state(original, 2, CODEC, {1: lambda value: integer(value) + 1}) == 5
    with pytest.raises(IncompatibleState):
        migrate_state(original, 2, CODEC, {})
    with pytest.raises(IncompatibleState):
        migrate_state(replace(original, schema=3), 2, CODEC, {})
    store.claim(original)
    with pytest.raises(IncompatibleState):
        DurableSession(
            view, initial=lambda: 0, codec=CODEC, store=store, identity=IDENTITY, schema=2
        )
    assert store.load(IDENTITY) == replace(original, owner_epoch=1)


def test_explicit_state_fresh_handlers_and_stale_epoch(store: StateStore) -> None:
    first = session(store)
    first.state.set(4)
    first.transport.sequence = 7
    first.transport.revision = 9
    first.checkpoint()
    old_epoch = first.transport.epoch
    old_handler = next(iter(first.rendered.handlers.values()))
    second = session(store)

    try:
        assert second.reconstructed
        assert second.state() == 4
        assert second.transport.epoch != old_epoch
        assert second.transport.accept({"epoch": old_epoch, "seq": 7, "state_rev": 9}) == "epoch"
        assert next(iter(second.rendered.handlers.values())) is not old_handler
        assert second.transport.sequence == 0
        with pytest.raises(RecoveryUnavailable):
            first.state.set(99)
        with pytest.raises(RecoveryUnavailable):
            first.checkpoint()
        assert first.closed
        assert second.state() == 4
    finally:
        first.dispose()
        second.dispose()


def test_explicit_state_crash_before_checkpoint_restores_committed_value(store: StateStore) -> None:
    first = session(store)
    first.state.set(99)
    first.dispose()
    restored = session(store)

    try:
        assert restored.state() == 0
    finally:
        restored.dispose()


def test_explicit_state_commit_before_ack_keeps_highwater(store: StateStore) -> None:
    async def exercise() -> None:
        first = session(store)
        async with first.runtime.turn():
            first.transport.sequence = 1
            first.state.set(8)
        committed = store.load(IDENTITY)
        assert committed is not None
        assert committed.payload == b"8"
        assert committed.sequence == 1
        assert committed.event_epoch == first.transport.epoch
        first.dispose()
        restored = session(store)

        try:
            assert restored.state() == 8
        finally:
            restored.dispose()

    asyncio.run(exercise())


def test_explicit_state_checkpoint_failure_invalidates_graph() -> None:
    class BrokenStore(MemoryStateStore):
        broken = False

        def commit(self, snapshot: StateSnapshot) -> StateSnapshot:
            if self.broken:
                raise RecoveryUnavailable("unavailable")

            return super().commit(snapshot)

    async def exercise() -> None:
        store = BrokenStore()
        first = session(store)
        store.broken = True
        with pytest.raises(RecoveryUnavailable):
            async with first.runtime.turn():
                first.state.set(8)
        assert first.closed
        assert first.pending == []
        assert first.runtime.closed
        with pytest.raises(RuntimeError, match="disposed"):
            first.state.set(9)
        store.broken = False
        restored = session(store)

        try:
            assert restored.state() == 0
        finally:
            restored.dispose()

    asyncio.run(exercise())


def test_explicit_state_identity_isolation(store: StateStore) -> None:
    original = store.claim(StateSnapshot(IDENTITY, 1, b"1"))

    for field in ("application", "session", "principal", "browser"):
        identity = replace(IDENTITY, **{field: "other"})
        assert store.load(identity) is None
        with pytest.raises(RecoveryUnavailable):
            store.commit(replace(original, identity=identity))


def test_explicit_state_oversized_mutation_is_refused_at_the_assignment() -> None:
    store = MemoryStateStore()
    graph = DurableSession(
        view,
        initial=lambda: 0,
        codec=StateCodec[int](lambda value: value, integer, max_bytes=4),
        store=store,
        identity=IDENTITY,
    )

    try:
        with pytest.raises(ValueError, match="byte limit"):
            graph.state.set(10000)
        assert not graph.closed
        assert not graph.recovery_failed
        assert graph.state() == 0
        graph.checkpoint()
        snapshot = store.load(IDENTITY)
        assert snapshot is not None
        assert snapshot.payload == b"0"
    finally:
        graph.dispose()


def test_explicit_state_unencodable_checkpoint_invalidates_graph() -> None:
    store = MemoryStateStore()
    refuse = [False]

    def encode(value: int) -> JSONValue:
        if refuse[0]:
            raise ValueError("state cannot be encoded")

        return value

    graph = DurableSession(
        view,
        initial=lambda: 0,
        codec=StateCodec[int](encode, integer),
        store=store,
        identity=IDENTITY,
    )
    refuse[0] = True
    with pytest.raises(RecoveryUnavailable, match="checkpoint failed"):
        graph.checkpoint()
    assert graph.closed
    assert graph.recovery_failed
    assert graph.pending == []
    snapshot = store.load(IDENTITY)
    assert snapshot is not None
    assert snapshot.payload == b"0"


def test_explicit_state_unreachable_store_withholds_output_without_fencing() -> None:
    class FlakyStore(MemoryStateStore):
        broken = False

        def commit(self, snapshot: StateSnapshot) -> StateSnapshot:
            if self.broken:
                raise StoreUnavailable("state store unavailable")

            return super().commit(snapshot)

    store = FlakyStore()
    graph = DurableSession(view, initial=lambda: 0, codec=CODEC, store=store, identity=IDENTITY)

    try:
        graph.state.set(1)
        store.broken = True
        with pytest.raises(StoreUnavailable):
            graph.checkpoint()
        assert not graph.closed
        assert not graph.recovery_failed
        committed = store.load(IDENTITY)
        assert committed is not None
        assert committed.payload == b"0"
        store.broken = False
        graph.checkpoint()
        restored = store.load(IDENTITY)
        assert restored is not None
        assert restored.payload == b"1"
    finally:
        graph.dispose()


def test_explicit_state_sqlite_reopen_and_atomic_fencing(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    first = SQLiteStateStore(path)
    claimed = first.claim(StateSnapshot(IDENTITY, 1, b"3"))
    reopened = SQLiteStateStore(path)
    assert reopened.load(IDENTITY) == claimed
    next_owner = reopened.claim(claimed)
    with pytest.raises(RecoveryUnavailable):
        first.commit(replace(claimed, payload=b"99"))
    assert first.load(IDENTITY) == next_owner
    with sqlite3.connect(path) as database:
        database.execute("BEGIN IMMEDIATE")
        with pytest.raises(RecoveryUnavailable):
            reopened.commit(next_owner)
    assert first.load(IDENTITY) == next_owner


def test_explicit_state_never_serializes_live_objects(store: StateStore) -> None:
    first = session(store)

    try:
        snapshot = store.load(IDENTITY)
        assert snapshot is not None
        assert json.loads(snapshot.payload) == 0
        assert b"handler" not in snapshot.payload
        with pytest.raises(ValueError, match="integer"):
            CODEC.loads(b'{"__reduce__": "anything"}')
    finally:
        first.dispose()


@pytest.mark.parametrize("commit", [False, True])
def test_explicit_state_real_process_crash(tmp_path: Path, commit: bool) -> None:
    path = tmp_path / "state.db"
    source = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "from dataclasses import replace\n"
        "from pysx import SQLiteStateStore, StateIdentity, StateSnapshot\n"
        "store = SQLiteStateStore(Path(sys.argv[1]))\n"
        "identity = StateIdentity('counter', 'session', 'principal', 'browser')\n"
        "snapshot = store.claim(StateSnapshot(identity, 1, b'0'))\n"
        "if sys.argv[2] == 'True':\n"
        "    store.commit(replace(snapshot, payload=b'9', event_epoch='old', sequence=3))\n"
        "os._exit(23)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", source, str(path), str(commit)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 23, result.stderr
    stored = SQLiteStateStore(path).load(IDENTITY)
    assert stored is not None
    assert stored.payload == (b"9" if commit else b"0")
    assert stored.sequence == (3 if commit else 0)


def test_explicit_state_rebuilds_owned_setup_and_cleanup(store: StateStore) -> None:
    setups: list[int] = []
    cleanups: list[int] = []

    def owned(state: Signal[int]) -> Fragment:
        on_setup(lambda: setups.append(1))
        on_cleanup(lambda: cleanups.append(1))

        return view(state)

    first = DurableSession(owned, initial=lambda: 0, codec=CODEC, store=store, identity=IDENTITY)
    first.dispose()
    second = DurableSession(owned, initial=lambda: 0, codec=CODEC, store=store, identity=IDENTITY)
    assert setups == [1, 1]
    assert cleanups == [1]
    second.dispose()
    second.dispose()
    assert cleanups == [1, 1]


def test_explicit_state_rebuilds_resources_without_duplicate_loaders(store: StateStore) -> None:
    async def exercise() -> None:
        started: list[int] = []
        cancelled: list[int] = []
        block = asyncio.Event()

        async def load() -> int:
            index = len(started)
            started.append(index)

            try:
                await block.wait()
            finally:
                cancelled.append(index)

            return 42

        def page(state: Signal[int]) -> Fragment:
            resource("profile", load)
            resource("profile", load)

            return view(state)

        first = DurableSession(page, initial=lambda: 0, codec=CODEC, store=store, identity=IDENTITY)
        await wait_for(lambda: len(started) == 1)
        first_tasks = tuple(first.runtime.tasks)
        first.dispose()
        await asyncio.gather(*first_tasks, return_exceptions=True)
        second = DurableSession(
            page, initial=lambda: 0, codec=CODEC, store=store, identity=IDENTITY
        )

        try:
            await wait_for(lambda: len(started) == 2)
            assert cancelled == [0]
            assert len(second.runtime.tasks) == 1
        finally:
            second_tasks = tuple(second.runtime.tasks)
            second.dispose()
            await asyncio.gather(*second_tasks, return_exceptions=True)
        assert cancelled == [0, 1]

    asyncio.run(exercise())


def test_explicit_state_does_not_capture_another_graphs_write_guard(store: StateStore) -> None:
    async def exercise() -> None:
        outer = Session(lambda: native.Div("outer"))
        async with outer.runtime.turn():
            inner = session(store)
        outer.dispose()

        try:
            await inner.runtime.update(lambda: inner.state.set(2))
            assert inner.state() == 2
        finally:
            tasks = tuple(inner.runtime.tasks)
            worker = inner.runtime.worker
            inner.dispose()

            if worker is not None:
                await asyncio.gather(worker, return_exceptions=True)
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_explicit_state_publication_checkpoints(background: bool, fail: bool) -> None:
    class CheckedStore(MemoryStateStore):
        broken = False

        def commit(self, snapshot: StateSnapshot) -> StateSnapshot:
            if self.broken:
                raise RecoveryUnavailable("store fault")

            return super().commit(snapshot)

    async def exercise() -> None:
        store = CheckedStore()

        class Runtime(HTTPRuntime):
            def create_reserved(
                self,
                request: RequestContext,
                *,
                location: str | None = None,
                browser: str | None = None,  # noqa: ARG002 - factory override
            ) -> Session:
                graph = DurableSession(
                    view,
                    initial=lambda: 0,
                    codec=CODEC,
                    store=store,
                    identity=IDENTITY,
                    request=request,
                    location=location or "/",
                )
                self.graphs.add(graph)

                return graph

        class Connection(FakeConnection):
            async def send(self, message: str) -> None:
                snapshot = store.load(IDENTITY)
                assert snapshot is not None
                frame: dict[str, object] = json.loads(message)
                assert snapshot.revision == frame["state_rev"]

                if frame["t"] in ("patch", "ack"):
                    assert snapshot.payload == b"1"
                    assert snapshot.sequence == (0 if background else 1)
                await super().send(message)

        runtime = Runtime(lambda: native.Div("unused"))
        connection = Connection("/ws?protocol=3", ['{"t":"hello","version":3}'])
        pump = asyncio.create_task(serve_connection(runtime, connection))

        try:
            await wait_for(lambda: bool(connection.frames("init")))
            graph = next(iter(runtime.graphs))
            assert isinstance(graph, DurableSession)
            graph = cast("DurableSession[int]", graph)
            store.broken = fail

            if background:
                if fail:
                    with pytest.raises(RecoveryUnavailable):
                        async with graph.runtime.turn():
                            graph.state.set(1)
                else:
                    await graph.runtime.update(lambda: graph.state.set(1))
            else:
                handler = next(iter(graph.rendered.handlers))
                connection.incoming.put_nowait(
                    json.dumps(
                        {
                            "t": "event",
                            "h": handler,
                            "v": None,
                            "version": 3,
                            "epoch": graph.transport.epoch,
                            "state_rev": graph.transport.revision,
                            "seq": 1,
                        }
                    )
                )

            if fail:
                await wait_for(lambda: graph.closed)
                await asyncio.wait_for(pump, 3)
                assert connection.closed == (1013, "durable state unavailable")
                assert not connection.frames("patch")
                assert not connection.frames("ack")
                snapshot = store.load(IDENTITY)
                assert snapshot is not None
                assert snapshot.payload == b"0"
            else:
                await wait_for(lambda: bool(connection.frames("patch")))

                if not background:
                    await wait_for(lambda: bool(connection.frames("ack")))
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            runtime.close()

    asyncio.run(exercise())
