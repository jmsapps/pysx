"""Atomic, expiring ownership of explicit application-state snapshots."""

from __future__ import annotations

import math
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, cast

from .persistence import (
    RecoveryUnavailable,
    SQLiteStateStore,
    StateIdentity,
    StateSnapshot,
    StoreUnavailable,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path
    from typing import Never


class OwnerMoved(RecoveryUnavailable):
    def __init__(self, worker: str) -> None:
        super().__init__("session belongs to another live owner")
        self.worker = worker


def validate_worker(worker: str) -> None:
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", worker) is None:
        raise ValueError("worker identifier must be a bounded URL-safe token")


def validate_lease(seconds: float) -> None:
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0.2 <= seconds <= 30:
        raise ValueError("owner lease must be between 0.2 and 30 seconds")


@dataclass(frozen=True)
class OwnerLease:
    identity: StateIdentity
    worker: str
    epoch: int
    expires: float


class OwnershipStore(Protocol):
    def load(self, identity: StateIdentity) -> StateSnapshot | None: ...
    def acquire(self, snapshot: StateSnapshot, worker: str, seconds: float) -> StateSnapshot: ...
    def save(self, snapshot: StateSnapshot, worker: str, seconds: float) -> StateSnapshot: ...
    def check_owner(self, snapshot: StateSnapshot, worker: str) -> None: ...
    def extend(self, worker: str, seconds: float) -> int: ...
    def renew(
        self, snapshots: Sequence[StateSnapshot], worker: str, seconds: float
    ) -> tuple[StateIdentity, ...]: ...
    def release_owner(self, snapshot: StateSnapshot, worker: str) -> None: ...
    def route(self, identity: StateIdentity) -> OwnerLease | None: ...
    def revoke(self, application: str, principal: str) -> None: ...


class SQLiteOwnershipStore(SQLiteStateStore):
    """Lease and snapshot changes share a local SQLite write transaction."""

    def __init__(
        self, path: Path, *, timeout: float = 0.1, clock: Callable[[], float] = time.time
    ) -> None:
        super().__init__(path, timeout=timeout)
        self.clock = clock
        self._transaction(
            lambda database: database.execute(
                "CREATE TABLE IF NOT EXISTS leases ("
                "application TEXT NOT NULL, session TEXT NOT NULL, principal TEXT NOT NULL, "
                "browser TEXT NOT NULL, worker TEXT NOT NULL, owner_epoch INTEGER NOT NULL, "
                "expires REAL NOT NULL, PRIMARY KEY(application, session, principal, browser))"
            )
        )
        self._transaction(
            lambda database: database.execute(
                "CREATE TABLE IF NOT EXISTS revoked_state (application TEXT NOT NULL, "
                "session TEXT NOT NULL, principal TEXT NOT NULL, browser TEXT NOT NULL, "
                "PRIMARY KEY(application, session, principal, browser))"
            )
        )

    @staticmethod
    def _unbound(snapshot: StateSnapshot) -> Never:
        del snapshot

        raise TypeError("ownership stores require a worker-bound adapter")

    def claim(self, snapshot: StateSnapshot) -> StateSnapshot:
        return self._unbound(snapshot)

    def commit(self, snapshot: StateSnapshot) -> StateSnapshot:
        return self._unbound(snapshot)

    def check(self, snapshot: StateSnapshot) -> None:
        self._unbound(snapshot)

    @staticmethod
    def _revoked(database: sqlite3.Connection, identity: StateIdentity) -> bool:
        return (
            database.execute(
                "SELECT 1 FROM revoked_state WHERE application=? AND session=? "
                "AND principal=? AND browser=?",
                SQLiteStateStore._key(identity),
            ).fetchone()
            is not None
        )

    def load(self, identity: StateIdentity) -> StateSnapshot | None:
        try:
            with closing(self._connect()) as database, database:
                database.execute("BEGIN")

                return None if self._revoked(database, identity) else self._read(database, identity)
        except sqlite3.Error as error:
            raise StoreUnavailable("ownership store unavailable") from error

    def _transaction[T](self, callback: Callable[[sqlite3.Connection], T]) -> T:
        try:
            with closing(self._connect()) as database, database:
                database.execute("BEGIN IMMEDIATE")

                return callback(database)
        except sqlite3.Error as error:
            raise StoreUnavailable("ownership store unavailable") from error

    @staticmethod
    def _lease(database: sqlite3.Connection, identity: StateIdentity) -> OwnerLease | None:
        row = database.execute(
            "SELECT worker, owner_epoch, expires FROM leases "
            "WHERE application=? AND session=? AND principal=? AND browser=?",
            SQLiteStateStore._key(identity),
        ).fetchone()

        if row is None:
            return None
        worker, epoch, expires = cast("tuple[str, int, float]", row)

        return OwnerLease(identity, worker, epoch, expires)

    def _valid(
        self, database: sqlite3.Connection, snapshot: StateSnapshot, worker: str, now: float
    ) -> bool:
        lease = self._lease(database, snapshot.identity)

        return (
            lease is not None
            and lease.worker == worker
            and lease.expires > now
            and lease.epoch == snapshot.owner_epoch
            and not self._revoked(database, snapshot.identity)
            and snapshot.matches(self._read(database, snapshot.identity))
        )

    @staticmethod
    def _put_lease(
        database: sqlite3.Connection, snapshot: StateSnapshot, worker: str, expires: float
    ) -> None:
        database.execute(
            "INSERT OR REPLACE INTO leases VALUES (?, ?, ?, ?, ?, ?, ?)",
            (*SQLiteStateStore._key(snapshot.identity), worker, snapshot.owner_epoch, expires),
        )

    def acquire(self, snapshot: StateSnapshot, worker: str, seconds: float) -> StateSnapshot:
        validate_worker(worker)
        validate_lease(seconds)

        def claim(database: sqlite3.Connection) -> StateSnapshot:
            now = self.clock()

            if self._revoked(database, snapshot.identity):
                raise RecoveryUnavailable("recovery credential revoked")
            lease = self._lease(database, snapshot.identity)

            if lease is not None and lease.expires > now and lease.worker != worker:
                raise OwnerMoved(lease.worker)
            current = self._read(database, snapshot.identity)

            if current is not None and not snapshot.matches(current):
                raise RecoveryUnavailable("state changed before owner claim")
            claimed = replace(snapshot, owner_epoch=snapshot.owner_epoch + 1)
            self._put(database, claimed)
            self._put_lease(database, claimed, worker, now + seconds)

            return claimed

        return self._transaction(claim)

    def save(self, snapshot: StateSnapshot, worker: str, seconds: float) -> StateSnapshot:
        validate_lease(seconds)

        def commit(database: sqlite3.Connection) -> StateSnapshot:
            now = self.clock()

            if not self._valid(database, snapshot, worker, now):
                raise RecoveryUnavailable("session owner has been fenced")
            committed = replace(snapshot, generation=snapshot.generation + 1)
            self._put(database, committed)
            self._put_lease(database, committed, worker, now + seconds)

            return committed

        return self._transaction(commit)

    def check_owner(self, snapshot: StateSnapshot, worker: str) -> None:
        try:
            with closing(self._connect()) as database, database:
                database.execute("BEGIN")

                if not self._valid(database, snapshot, worker, self.clock()):
                    raise RecoveryUnavailable("session owner has been fenced")
        except sqlite3.Error as error:
            raise StoreUnavailable("ownership store unavailable") from error

    def extend(self, worker: str, seconds: float) -> int:
        """Push this worker's own lease expiries out, without judging ownership.

        `renew` answers two questions at once: extend my leases, and tell me
        which I lost. The second needs a per-session state comparison, and
        paying for it on every extension is what lets a busy worker starve the
        renewal it depends on. Extending rows that already name this worker is
        safe on its own: a claim by anyone else replaces the worker and epoch in
        the same serialized transaction, so those rows stop matching here.
        """
        validate_worker(worker)
        validate_lease(seconds)

        def refresh(database: sqlite3.Connection) -> int:
            updated = database.execute(
                "UPDATE leases SET expires = ? WHERE worker = ? AND expires > ?",
                (self.clock() + seconds, worker, self.clock()),
            )

            return updated.rowcount

        return self._transaction(refresh)

    def renew(
        self, snapshots: Sequence[StateSnapshot], worker: str, seconds: float
    ) -> tuple[StateIdentity, ...]:
        validate_lease(seconds)

        def refresh(database: sqlite3.Connection) -> tuple[StateIdentity, ...]:
            for table in ("state", "leases", "revoked_state"):
                database.execute(f"SELECT 1 FROM {table} LIMIT 0")
            now = self.clock()
            fenced: list[StateIdentity] = []

            for snapshot in snapshots:
                if self._valid(database, snapshot, worker, now):
                    self._put_lease(database, snapshot, worker, now + seconds)
                else:
                    fenced.append(snapshot.identity)

            return tuple(fenced)

        return self._transaction(refresh)

    def release_owner(self, snapshot: StateSnapshot, worker: str) -> None:
        def release(database: sqlite3.Connection) -> None:
            database.execute(
                "DELETE FROM leases WHERE application=? AND session=? AND principal=? "
                "AND browser=? AND worker=? AND owner_epoch=?",
                (*self._key(snapshot.identity), worker, snapshot.owner_epoch),
            )

        self._transaction(release)

    def route(self, identity: StateIdentity) -> OwnerLease | None:
        try:
            with closing(self._connect()) as database:
                lease = self._lease(database, identity)

                return lease if lease is not None and lease.expires > self.clock() else None
        except sqlite3.Error as error:
            raise StoreUnavailable("ownership store unavailable") from error

    def revoke(self, application: str, principal: str) -> None:
        def invalidate(database: sqlite3.Connection) -> None:
            database.execute(
                "INSERT OR IGNORE INTO revoked_state SELECT application, session, principal, "
                "browser FROM state WHERE application=? AND principal=?",
                (application, principal),
            )
            database.execute(
                "DELETE FROM leases WHERE application=? AND principal=?",
                (application, principal),
            )

        self._transaction(invalidate)


class LeasedStateStore:
    """Adapt a worker's lease policy to the ordinary checkpoint contract."""

    def __init__(self, backend: OwnershipStore, worker: str, seconds: float = 3) -> None:
        validate_worker(worker)
        validate_lease(seconds)
        self.backend = backend
        self.worker = worker
        self.seconds = seconds

    def load(self, identity: StateIdentity) -> StateSnapshot | None:
        return self.backend.load(identity)

    def claim(self, snapshot: StateSnapshot) -> StateSnapshot:
        return self.backend.acquire(snapshot, self.worker, self.seconds)

    def commit(self, snapshot: StateSnapshot) -> StateSnapshot:
        return self.backend.save(snapshot, self.worker, self.seconds)

    def check(self, snapshot: StateSnapshot) -> None:
        self.backend.check_owner(snapshot, self.worker)
