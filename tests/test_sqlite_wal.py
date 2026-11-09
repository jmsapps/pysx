"""Retained WAL connections preserve FULL durability and explicit ownership."""

import gc
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from typing import TYPE_CHECKING, cast
from weakref import ref

import pytest
from test_state_recovery import IDENTITY

from pysx import (
    RecoveryUnavailable,
    SQLiteOwnershipStore,
    SQLiteStateStore,
    StateSnapshot,
    StoreUnavailable,
)

if TYPE_CHECKING:
    from pathlib import Path


class ObservedStore(SQLiteStateStore):
    def __init__(self, path: Path, *, timeout: float = 0.1) -> None:
        self.connections: list[sqlite3.Connection] = []
        super().__init__(path, timeout=timeout, wal=True)

    def _connect(self) -> sqlite3.Connection:
        database = super()._connect()
        self.connections.append(database)

        return database

    def configuration(self) -> tuple[str, int]:
        with self._database() as database:
            journal = cast("tuple[str]", database.execute("PRAGMA journal_mode").fetchone())[0]
            synchronous = cast("tuple[int]", database.execute("PRAGMA synchronous").fetchone())[0]

            return journal, synchronous

    def hold(self, ready: Event, release: Event) -> None:
        with self._database() as database, database:
            database.execute("BEGIN IMMEDIATE")
            ready.set()
            assert release.wait(5), "test did not release its transaction"


def test_wal_retains_one_full_connection_and_rolls_back_stale_writes(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with ObservedStore(path) as store:
        first = store.claim(StateSnapshot(IDENTITY, 1, b"0"))
        committed = store.commit(replace(first, payload=b"1"))
        with pytest.raises(RecoveryUnavailable):
            store.commit(first)
        assert store.load(IDENTITY) == committed
        assert store.configuration() == ("wal", 2)
        assert len(store.connections) == 1
        connection = store.connections[0]
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    with pytest.raises(StoreUnavailable, match="closed"):
        store.load(IDENTITY)
    with SQLiteStateStore(path) as reopened:
        assert reopened.load(IDENTITY) == committed
    path.unlink()


def test_wal_serializes_shared_connection_across_threads(tmp_path: Path) -> None:
    with ObservedStore(tmp_path / "state.db", timeout=1) as store:
        def write(index: int) -> StateSnapshot:
            identity = replace(IDENTITY, session=f"session-{index}")
            claimed = store.claim(StateSnapshot(identity, 1, b"0"))

            return store.commit(replace(claimed, payload=str(index).encode()))

        with ThreadPoolExecutor(max_workers=8) as executor:
            snapshots = list(executor.map(write, range(24)))
        assert all(store.load(snapshot.identity) == snapshot for snapshot in snapshots)
        assert len(store.connections) == 1


def test_wal_lock_wait_and_close_are_bounded_without_poisoning_connection(tmp_path: Path) -> None:
    with ObservedStore(tmp_path / "state.db", timeout=0.05) as store:
        ready, release = Event(), Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            held = executor.submit(store.hold, ready, release)

            try:
                assert ready.wait(2)
                with pytest.raises(StoreUnavailable, match="busy"):
                    store.load(IDENTITY)
                with pytest.raises(StoreUnavailable, match="busy"):
                    store.close()
            finally:
                release.set()
                held.result(timeout=2)
        assert store.load(IDENTITY) is None
        assert store.configuration() == ("wal", 2)


@pytest.mark.parametrize("table", ["state", "leases"])
def test_wal_constructor_failure_releases_owned_connection(tmp_path: Path, table: str) -> None:
    opened: list[sqlite3.Connection] = []

    def deny_schema(
        action: int,
        name: str | None,
        _column: str | None,
        _database: str | None,
        _source: str | None,
    ) -> int:
        return (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == table
            else sqlite3.SQLITE_OK
        )

    class BrokenStore(SQLiteOwnershipStore):
        def _connect(self) -> sqlite3.Connection:
            database = super()._connect()
            opened.append(database)
            database.set_authorizer(deny_schema)

            return database

    path = tmp_path / "state.db"
    error = sqlite3.DatabaseError if table == "state" else StoreUnavailable
    with pytest.raises(error):
        BrokenStore(path, wal=True)
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")
    path.unlink()


def test_wal_committed_state_survives_exit_without_finalizers(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    script = """
import os, sys
from dataclasses import replace
from pathlib import Path
from pysx import SQLiteStateStore, StateIdentity, StateSnapshot
store = SQLiteStateStore(Path(sys.argv[1]), wal=True)
identity = StateIdentity('app', 'session', 'principal', 'browser')
snapshot = store.claim(StateSnapshot(identity, 1, b'0'))
store.commit(replace(snapshot, payload=b'committed'))
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script, str(path)], check=True, timeout=10)
    assert path.with_name(path.name + "-wal").is_file()
    with SQLiteStateStore(path) as recovered:
        snapshot = recovered.load(replace(IDENTITY, application="app"))
        assert snapshot is not None
        assert snapshot.payload == b"committed"
        assert snapshot.generation == 1


def test_wal_finalizer_releases_connection_without_retaining_store(tmp_path: Path) -> None:
    store = ObservedStore(tmp_path / "state.db")
    connection = store.connections[0]
    reference = ref(store)
    del store
    gc.collect()
    assert reference() is None
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_wal_existing_database_cannot_bypass_version_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    with SQLiteStateStore(path, wal=True):
        pass
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 50, 4))
    with pytest.raises(StoreUnavailable, match="WAL-reset"):
        SQLiteStateStore(path)


@pytest.mark.parametrize(
    ("version", "allowed"),
    [
        ((3, 44, 5), False),
        ((3, 44, 6), True),
        ((3, 50, 4), False),
        ((3, 50, 7), True),
        ((3, 51, 2), False),
        ((3, 51, 3), True),
    ],
)
def test_wal_requires_the_sqlite_reset_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: tuple[int, int, int], allowed: bool
) -> None:
    monkeypatch.setattr(sqlite3, "sqlite_version_info", version)
    path = tmp_path / "state.db"

    if allowed:
        with SQLiteStateStore(path, wal=True) as store:
            assert store.load(IDENTITY) is None
    else:
        with pytest.raises(ValueError, match="WAL-reset"):
            SQLiteStateStore(path, wal=True)
        assert not path.exists()
