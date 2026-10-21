"""Real process loss, lease fencing, principal binding and recovery headroom."""

import asyncio
import hashlib
import json
import time
from dataclasses import replace
from typing import TYPE_CHECKING, cast

import pytest
from ownership_topology import Client, Workers, get
from test_connection_pump import FakeConnection
from test_state_recovery import CODEC, IDENTITY, view
from websockets.exceptions import ConnectionClosed, InvalidStatus

from pysx import (
    DurableApplication,
    DurableSession,
    RecoveryUnavailable,
    SQLiteOwnershipStore,
    SQLiteStateStore,
    StateIdentity,
    StateSnapshot,
    StoreUnavailable,
)
from pysx.runtime.connection import serve_connection
from pysx.runtime.ownership import LeasedStateStore, OwnerMoved

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ownership_topology import Worker


def identity(client: Client, user: str = "alice") -> StateIdentity:
    return StateIdentity(
        "worker-counter",
        client.recovery,
        hashlib.sha256(b"principal:" + user.encode("utf-8", "surrogatepass")).hexdigest(),
        client.browser,
    )


async def recover(worker: Worker, previous: Client) -> Client:
    async with asyncio.timeout(10):
        while True:
            try:
                return await Client.attach(worker, previous=previous)
            except ConnectionClosed as error:
                if error.rcvd is None or error.rcvd.code not in (1013, 4010):
                    raise
                await asyncio.sleep(0.05)


async def attach_many(workers: list[Worker]) -> list[Client]:
    clients: list[Client] = []

    for offset in range(0, len(workers), 20):
        results = await asyncio.gather(
            *(Client.attach(worker) for worker in workers[offset : offset + 20]),
            return_exceptions=True,
        )
        clients.extend(result for result in results if isinstance(result, Client))
        errors = [result for result in results if isinstance(result, BaseException)]

        if errors:
            await asyncio.gather(*(client.close() for client in clients))

            raise errors[0]

    return clients


async def seed_workload(clients: list[Client]) -> None:
    for offset in range(0, len(clients), 20):
        cohort = clients[offset : offset + 20]
        began = time.monotonic()
        await asyncio.gather(*(client.event() for client in cohort))
        await asyncio.sleep(max(0, began + 0.5 - time.monotonic()))
        await asyncio.gather(
            *(client.event(handler=client.bindings[0], value="preserved edit") for client in cohort)
        )
        await asyncio.sleep(max(0, began + 1 - time.monotonic()))
        await asyncio.gather(*(client.event(handler=client.handlers[1]) for client in cohort))
        await asyncio.sleep(max(0, began + 1.5 - time.monotonic()))


def test_owner_routing_atomic_claim_expiry_renewal_and_stale_writes(tmp_path: Path) -> None:
    now = [100.0]
    backend = SQLiteOwnershipStore(tmp_path / "state.db", clock=lambda: now[0])
    first_store = LeasedStateStore(backend, "worker-a", 1)
    first = DurableSession(
        view, initial=lambda: 0, codec=CODEC, store=first_store, identity=IDENTITY
    )
    first.state.set(3)
    first.checkpoint()
    lease = backend.route(IDENTITY)
    assert lease is not None
    assert lease.worker == "worker-a"
    with pytest.raises(OwnerMoved, match="live owner"):
        LeasedStateStore(backend, "worker-b", 1).claim(first.snapshot)
    now[0] = 100.9
    assert backend.renew([first.snapshot], "worker-a", 1) == ()
    now[0] = 101.1
    first_store.check(first.snapshot)
    now[0] = 102
    assert backend.route(IDENTITY) is None
    with pytest.raises(RecoveryUnavailable, match="fenced"):
        first.state.set(9)
    second = DurableSession(
        view,
        initial=lambda: 0,
        codec=CODEC,
        store=LeasedStateStore(backend, "worker-b", 1),
        identity=IDENTITY,
    )

    try:
        assert second.state() == 3
        assert second.snapshot.owner_epoch == first.snapshot.owner_epoch + 1
        assert backend.renew([first.snapshot], "worker-a", 1) == (IDENTITY,)
        backend.release_owner(first.snapshot, "worker-a")
        assert backend.route(IDENTITY) is not None
        with pytest.raises(RecoveryUnavailable, match="checkpoint"):
            first.checkpoint()
        assert first.closed
    finally:
        first.dispose()
        second.dispose()


def test_owner_routing_unbound_backend_cannot_bypass_leases(tmp_path: Path) -> None:
    backend = SQLiteOwnershipStore(tmp_path / "state.db")
    claimed = LeasedStateStore(backend, "worker-a").claim(StateSnapshot(IDENTITY, 1, b"1"))

    for operation in (backend.claim, backend.commit, backend.check):
        with pytest.raises(TypeError, match="worker-bound"):
            operation(claimed)


def test_owner_routing_stale_queued_and_async_mutations_are_fenced(tmp_path: Path) -> None:
    async def exercise() -> None:
        now = [100.0]
        backend = SQLiteOwnershipStore(tmp_path / "state.db", clock=lambda: now[0])
        first = DurableSession(
            view,
            initial=lambda: 0,
            codec=CODEC,
            store=LeasedStateStore(backend, "worker-a", 1),
            identity=IDENTITY,
        )
        now[0] += 2
        second = DurableSession(
            view,
            initial=lambda: 0,
            codec=CODEC,
            store=LeasedStateStore(backend, "worker-b", 1),
            identity=IDENTITY,
        )
        ran: list[bool] = []
        with pytest.raises(RecoveryUnavailable, match="ownership"):
            await first.runtime.update(lambda: ran.append(True))
        assert ran == []
        assert first.closed
        assert second.state() == 0
        second.dispose()

        if first.runtime.worker is not None:
            await asyncio.gather(first.runtime.worker, return_exceptions=True)

    asyncio.run(exercise())


def test_owner_routing_real_process_loss_and_principal_binding(tmp_path: Path) -> None:
    with Workers(tmp_path) as topology:
        async def exercise() -> None:
            first, survivor = topology.workers
            client = await Client.attach(first)
            await client.event()
            backend = SQLiteOwnershipStore(topology.store)
            saved = backend.load(identity(client))
            assert saved is not None
            assert saved.payload == b"1"
            first.stop(kill=True)
            with pytest.raises(ConnectionClosed) as denied:
                await Client.attach(survivor, previous=client, user="bob")
            assert denied.value.rcvd is not None
            assert denied.value.rcvd.code == 4004
            restored = await recover(survivor, client)

            try:
                assert restored.epoch != client.epoch
                assert restored.recovery == client.recovery
                assert restored.owner == survivor.identifier
                await restored.event()
                current = backend.load(identity(restored))
                assert current is not None
                assert current.payload == b"2"
                with pytest.raises(RecoveryUnavailable, match="fenced"):
                    backend.save(replace(saved, payload=b"99"), first.identifier, 1)
            finally:
                await restored.close()
                await client.close()

        asyncio.run(exercise())


def test_owner_routing_real_two_process_reclaim_race(tmp_path: Path) -> None:
    with Workers(tmp_path) as topology:
        async def exercise() -> None:
            original, survivor = topology.workers
            client = await Client.attach(original)
            await client.event()
            original.stop(kill=True)
            replacement = topology.start()
            await asyncio.sleep(1.1)
            attempts = await asyncio.gather(
                Client.attach(survivor, previous=client),
                Client.attach(replacement, previous=client),
                return_exceptions=True,
            )
            won = [result for result in attempts if isinstance(result, Client)]
            lost = [result for result in attempts if isinstance(result, ConnectionClosed)]
            assert len(won) == 1, attempts
            assert len(lost) == 1, attempts
            assert lost[0].rcvd is not None
            assert lost[0].rcvd.code == 4010
            winner = won[0]

            try:
                await winner.event()
                snapshot = SQLiteOwnershipStore(topology.store).load(identity(winner))
                assert snapshot is not None
                assert snapshot.payload == b"2"
                assert snapshot.owner_epoch == 2
            finally:
                await winner.close()
                await client.close()

        asyncio.run(exercise())


def test_owner_routing_fifty_sessions_per_worker_survive_owner_death(tmp_path: Path) -> None:
    with Workers(tmp_path, workload=True) as topology:
        async def exercise() -> None:
            failed, survivor = topology.workers
            clients = await attach_many([failed] * 50 + [survivor] * 50)
            restored: list[Client] = []

            try:
                assert all(len(client.bindings) == 100 for client in clients)
                await seed_workload(clients)
                failed.stop(kill=True)
                began = time.monotonic()
                restored = await asyncio.gather(
                    *(recover(survivor, client) for client in clients[:50])
                )
                assert time.monotonic() - began <= 10
                await asyncio.gather(*(client.event() for client in restored))
                backend = SQLiteOwnershipStore(topology.store)

                for client in restored:
                    snapshot = backend.load(identity(client))
                    assert snapshot is not None
                    data = json.loads(snapshot.payload)
                    assert data["count"] == 2
                    assert data["rows"][0] == "preserved edit"
                    assert data["order"] == list(reversed(range(100)))
                    assert data["theme"] == "dark"
                    assert data["location"] == "/workload/reordered"
                    assert len(client.bindings) == 100
                    lease = backend.route(identity(client))
                    assert lease is not None
                    assert lease.worker == survivor.identifier

                for client in clients[50:]:
                    snapshot = backend.load(identity(client))
                    assert snapshot is not None
                    assert json.loads(snapshot.payload)["count"] == 1
            finally:
                await asyncio.gather(*(client.close() for client in [*clients, *restored]))

        asyncio.run(exercise())


def test_owner_routing_full_capacity_refuses_recovery_until_replacement(tmp_path: Path) -> None:
    with Workers(tmp_path, workload=True) as topology:
        async def exercise() -> None:
            failed, survivor = topology.workers
            clients = await attach_many([failed] * 100 + [survivor] * 100)
            restored: Client | None = None

            try:
                await clients[0].event()
                failed.stop(kill=True)
                with pytest.raises(InvalidStatus) as refused:
                    await Client.attach(survivor, previous=clients[0])
                assert refused.value.response.status_code == 503
                replacement = topology.start()
                restored = await recover(replacement, clients[0])
                snapshot = SQLiteOwnershipStore(topology.store).load(identity(restored))
                assert snapshot is not None
                assert json.loads(snapshot.payload)["count"] == 1
            finally:
                await asyncio.gather(*(client.close() for client in clients))

                if restored is not None:
                    await restored.close()

        asyncio.run(exercise())


def test_owner_routing_live_stale_async_completion_cannot_mutate_recovered_state(
    tmp_path: Path,
) -> None:
    with Workers(tmp_path) as topology:
        async def exercise() -> None:
            stale, survivor = topology.workers
            client = await Client.attach(stale)
            await client.event()
            assert get(stale.port, "/_fixture/pause")[0] == 200
            await client.event(slow=True, wait=False)
            restored = await recover(survivor, client)

            try:
                await asyncio.wait_for(client.socket.wait_closed(), 4)
                assert client.socket.close_code == 1013
                snapshot = SQLiteOwnershipStore(topology.store).load(identity(restored))
                assert snapshot is not None
                assert snapshot.payload == b"1"
                await restored.event()
                snapshot = SQLiteOwnershipStore(topology.store).load(identity(restored))
                assert snapshot is not None
                assert snapshot.payload == b"2"
            finally:
                await restored.close()
                await client.close()

        asyncio.run(exercise())


def test_owner_routing_lost_store_fails_closed_and_restores_last_commit(tmp_path: Path) -> None:
    with Workers(tmp_path) as topology:
        async def exercise() -> None:
            original, survivor = topology.workers
            client = await Client.attach(original)
            await client.event()
            saved = topology.store.with_suffix(".saved")
            topology.store.rename(saved)

            try:
                await asyncio.wait_for(client.socket.wait_closed(), 4)
                assert client.socket.close_code == 1013
                with pytest.raises(ConnectionClosed) as unavailable:
                    await Client.attach(survivor, previous=client)
                assert unavailable.value.rcvd is not None
                assert unavailable.value.rcvd.code == 1013
            finally:
                topology.store.unlink(missing_ok=True)
                saved.rename(topology.store)
            restored = await recover(original, client)

            try:
                assert restored.owner == original.identifier
                snapshot = SQLiteOwnershipStore(topology.store).load(identity(restored))
                assert snapshot is not None
                assert snapshot.payload == b"1"
            finally:
                await restored.close()
                await client.close()

        asyncio.run(exercise())


def test_owner_routing_rolling_drain_and_compatible_schema_migration(tmp_path: Path) -> None:
    with Workers(tmp_path) as topology:
        async def exercise() -> None:
            original = topology.workers[0]
            client = await Client.attach(original)
            await client.event()
            assert get(original.port, "/_fixture/drain")[0] == 200
            with pytest.raises(InvalidStatus) as refused:
                await Client.attach(original)
            assert refused.value.response.status_code == 503
            await client.event()
            original.stop()
            replacement = topology.start(port=original.port, schema=2, migrate=True)
            restored = await recover(replacement, client)

            try:
                assert restored.recovery == client.recovery
                snapshot = SQLiteOwnershipStore(topology.store).load(identity(restored))
                assert snapshot is not None
                assert snapshot.schema == 2
                assert snapshot.payload == b"2"
                await restored.event()
            finally:
                await restored.close()
                await client.close()

        asyncio.run(exercise())


@pytest.mark.parametrize("fresh", [False, True])
def test_owner_routing_incompatible_upgrade_requires_explicit_policy(
    tmp_path: Path, fresh: bool
) -> None:
    with Workers(tmp_path) as topology:
        async def exercise() -> None:
            original = topology.workers[0]
            client = await Client.attach(original)
            await client.event()
            original.stop()
            replacement = topology.start(schema=2, fresh=fresh)

            if fresh:
                restored = await Client.attach(replacement, previous=client)

                try:
                    assert restored.recovery != client.recovery
                    snapshot = SQLiteOwnershipStore(topology.store).load(identity(restored))
                    assert snapshot is not None
                    assert snapshot.payload == b"0"
                    assert snapshot.schema == 2
                finally:
                    await restored.close()
            else:
                with pytest.raises(ConnectionClosed) as unavailable:
                    await Client.attach(replacement, previous=client)
                assert unavailable.value.rcvd is not None
                assert unavailable.value.rcvd.code == 1013
            original_state = SQLiteOwnershipStore(topology.store).load(identity(client))
            assert original_state is not None
            assert original_state.payload == b"1"
            assert original_state.schema == 1
            await client.close()

        asyncio.run(exercise())


def test_owner_routing_revocation_invalidates_credentials_across_workers(tmp_path: Path) -> None:
    with Workers(tmp_path) as topology:
        async def exercise() -> None:
            first, second = topology.workers
            clients = await attach_many([first, second])
            fresh: Client | None = None

            try:
                await asyncio.gather(*(client.event() for client in clients))
                assert get(first.port, "/_fixture/revoke")[0] == 200
                await asyncio.wait_for(
                    asyncio.gather(*(client.socket.wait_closed() for client in clients)), 4
                )

                for client in clients:
                    with pytest.raises(ConnectionClosed) as refused:
                        await Client.attach(second, previous=client)
                    assert refused.value.rcvd is not None
                    assert refused.value.rcvd.code == 4004
                    assert SQLiteOwnershipStore(topology.store).load(identity(client)) is None
                fresh = await Client.attach(second)
                state = SQLiteOwnershipStore(topology.store).load(identity(fresh))
                assert state is not None
                assert state.payload == b"0"
            finally:
                await asyncio.gather(*(client.close() for client in clients))

                if fresh is not None:
                    await fresh.close()

        asyncio.run(exercise())


@pytest.mark.parametrize("payload", [b'"wrong shape"', b"not-json"])
def test_owner_routing_malformed_snapshot_never_implicitly_starts_fresh(
    tmp_path: Path, payload: bytes
) -> None:
    with Workers(tmp_path) as topology:
        async def exercise() -> None:
            original = topology.workers[0]
            client = await Client.attach(original)
            await client.event()
            original.stop()
            raw_store = SQLiteStateStore(topology.store)
            snapshot = raw_store.load(identity(client))
            assert snapshot is not None
            raw_store.commit(replace(snapshot, payload=payload))
            replacement = topology.start(fresh=True)
            with pytest.raises(ConnectionClosed) as unavailable:
                await Client.attach(replacement, previous=client)
            assert unavailable.value.rcvd is not None
            assert unavailable.value.rcvd.code == 1013
            unchanged = raw_store.load(identity(client))
            assert unchanged is not None
            assert unchanged.payload == payload
            await client.close()

        asyncio.run(exercise())


def durable_host(backend: SQLiteOwnershipStore, lease: float = 3) -> DurableApplication[int]:
    return DurableApplication(
        view,
        initial=lambda: 0,
        codec=CODEC,
        store=backend,
        application="worker-counter",
        worker="worker-a",
        lease_seconds=lease,
    )


def test_owner_routing_incomplete_resume_hello_never_reaches_reconstruction(tmp_path: Path) -> None:
    async def exercise() -> None:
        backend = SQLiteOwnershipStore(tmp_path / "state.db")
        host = durable_host(backend)
        runtime = host.runtime
        probe = FakeConnection("/ws?protocol=3", [])
        session = runtime.create_reserved(probe.request)
        resume = runtime.attach(session, probe.request)
        runtime.detach(session)
        await session.runtime.update(lambda: cast("DurableSession[int]", session).state.set(5))
        hello = json.dumps(
            {
                "t": "hello",
                "version": 3,
                "resume": resume,
                "recovery": runtime.recovery_token(session),
                "state_rev": 0,
            }
        )
        connection = FakeConnection("/ws?protocol=3&resume=1", [hello])

        try:
            await serve_connection(runtime, connection)
            assert connection.closed == (4004, "resume unavailable")
            assert runtime.detached[resume].session is session
            assert not session.closed
            assert len(runtime.graphs) == 1
        finally:
            await host.close()

    asyncio.run(exercise())


def test_owner_routing_reconstruction_on_the_owning_worker_is_not_redirected(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        backend = SQLiteOwnershipStore(tmp_path / "state.db")
        host = durable_host(backend)
        runtime = host.runtime
        probe = FakeConnection("/ws?protocol=3", [])
        session = runtime.create_reserved(probe.request)
        await session.runtime.update(lambda: cast("DurableSession[int]", session).state.set(6))
        credential = runtime.recovery_token(session)
        assert credential is not None
        lease = backend.route(cast("DurableSession[int]", session).snapshot.identity)
        assert lease is not None
        assert lease.worker == "worker-a"
        rebuilt = runtime.recover(credential, probe.request, location="/")

        try:
            assert rebuilt is not session
            assert session.closed
            assert cast("DurableSession[int]", rebuilt).state() == 6
            assert len(runtime.graphs) == 1
        finally:
            await host.close()

    asyncio.run(exercise())


def test_owner_routing_unreachable_store_fences_only_after_the_lease_lapses(
    tmp_path: Path,
) -> None:
    class FlakyBackend(SQLiteOwnershipStore):
        broken = False

        def renew(
            self, snapshots: Sequence[StateSnapshot], worker: str, seconds: float
        ) -> tuple[StateIdentity, ...]:
            if self.broken:
                raise StoreUnavailable("ownership store unavailable")

            return super().renew(snapshots, worker, seconds)

    async def exercise() -> None:
        backend = FlakyBackend(tmp_path / "state.db")
        host = durable_host(backend, lease=0.6)
        await host.start()
        probe = FakeConnection("/ws?protocol=3", [])
        session = host.runtime.create_reserved(probe.request)

        try:
            backend.broken = True
            await asyncio.sleep(0.3)
            assert host.store_failures_total >= 1
            assert host.fenced_total == 0
            assert not session.closed
            await asyncio.sleep(0.6)
            assert host.fenced_total == 1
            assert session.closed
            assert cast("DurableSession[int]", session).recovery_failed
        finally:
            backend.broken = False
            await host.close()

    asyncio.run(exercise())
