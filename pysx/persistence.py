"""Explicit application-state codecs and atomic, identity-bound checkpoints."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from threading import RLock
from typing import TYPE_CHECKING, Protocol, cast

from .wire import JSONValue

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path


class RecoveryUnavailable(OSError):  # noqa: N818 - public recovery outcome
    """A checkpoint or ownership operation could not complete safely."""


class StoreUnavailable(RecoveryUnavailable):
    """The store could not be reached; ownership has not been observed to change."""


class IncompatibleState(ValueError):  # noqa: N818 - public recovery outcome
    """Stored application state requires an explicit migration or reset."""


def _validate(value: object, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError("state exceeds the nesting limit")

    if value is None or type(value) in (bool, str):
        return

    if type(value) is int:
        if abs(value) > 2**53 - 1:
            raise ValueError("state integer exceeds the interoperable JSON range")

        return

    if type(value) is float and math.isfinite(value):
        return

    if type(value) is list:
        items = cast("list[object]", value)

        if len(items) > 4096:
            raise ValueError("state collection exceeds the entry limit")

        for item in items:
            _validate(item, depth + 1)

        return

    if type(value) is dict:
        fields = cast("dict[object, object]", value)

        if len(fields) > 4096 or any(type(key) is not str for key in fields):
            raise ValueError("state object requires bounded string keys")

        for item in fields.values():
            _validate(item, depth + 1)

        return

    raise ValueError("state codec must produce finite, bounded JSON values")


@dataclass(frozen=True)
class StateCodec[T]:
    """Application-owned conversion; decoding never imports or executes stored code."""

    encode: Callable[[T], JSONValue]
    decode: Callable[[JSONValue], T]
    max_bytes: int = 65536

    def __post_init__(self) -> None:
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 1048576:
            raise ValueError("codec max_bytes must be between 1 and 1048576")

    def dumps(self, value: T) -> bytes:
        encoded = self.encode(value)
        _validate(encoded)
        payload = json.dumps(encoded, ensure_ascii=True, allow_nan=False).encode("ascii")

        if len(payload) > self.max_bytes:
            raise ValueError("state exceeds the byte limit")

        return payload

    def loads(self, payload: bytes) -> T:
        if len(payload) > self.max_bytes:
            raise ValueError("state exceeds the byte limit")

        try:
            value: object = json.loads(payload)
            _validate(value)
        except (RecursionError, UnicodeError) as error:
            raise ValueError("invalid state JSON") from error

        return self.decode(cast("JSONValue", value))


@dataclass(frozen=True)
class StateIdentity:
    application: str
    session: str
    principal: str
    browser: str

    def __post_init__(self) -> None:
        for value in (self.application, self.session, self.principal, self.browser):
            if type(value) is not str or not 1 <= len(value) <= 512:
                raise ValueError("state identities require nonempty bounded strings")


@dataclass(frozen=True)
class StateSnapshot:
    identity: StateIdentity
    schema: int
    payload: bytes
    owner_epoch: int = 0
    generation: int = 0
    event_epoch: str = ""
    sequence: int = 0
    revision: int = 0

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema < 1:
            raise ValueError("state schema must be a positive integer")

        if type(self.payload) is not bytes or len(self.payload) > 1048576:
            raise ValueError("snapshot payload exceeds the store limit")

        if any(
            type(n) is not int or n < 0
            for n in (self.owner_epoch, self.generation, self.sequence, self.revision)
        ):
            raise ValueError("snapshot counters must be nonnegative integers")

        if type(self.event_epoch) is not str or len(self.event_epoch) > 512:
            raise ValueError("invalid snapshot event epoch")

    def matches(self, current: StateSnapshot | None) -> bool:
        return current is not None and (
            current.identity == self.identity
            and current.owner_epoch == self.owner_epoch
            and current.generation == self.generation
        )


class StateStore(Protocol):
    def load(self, identity: StateIdentity) -> StateSnapshot | None: ...
    def claim(self, snapshot: StateSnapshot) -> StateSnapshot: ...
    def commit(self, snapshot: StateSnapshot) -> StateSnapshot: ...
    def check(self, snapshot: StateSnapshot) -> None: ...


class MemoryStateStore:
    """Thread-safe contract adapter; deliberately provides no restart durability."""

    def __init__(self) -> None:
        self._rows: dict[StateIdentity, StateSnapshot] = {}
        self._lock = RLock()

    def load(self, identity: StateIdentity) -> StateSnapshot | None:
        with self._lock:
            return self._rows.get(identity)

    def claim(self, snapshot: StateSnapshot) -> StateSnapshot:
        with self._lock:
            current = self._rows.get(snapshot.identity)

            if current is not None and not snapshot.matches(current):
                raise RecoveryUnavailable("state changed before ownership claim")
            claimed = replace(snapshot, owner_epoch=snapshot.owner_epoch + 1)
            self._rows[snapshot.identity] = claimed

            return claimed

    def commit(self, snapshot: StateSnapshot) -> StateSnapshot:
        with self._lock:
            self.check(snapshot)
            committed = replace(snapshot, generation=snapshot.generation + 1)
            self._rows[snapshot.identity] = committed

            return committed

    def check(self, snapshot: StateSnapshot) -> None:
        with self._lock:
            if not snapshot.matches(self._rows.get(snapshot.identity)):
                raise RecoveryUnavailable("state owner has been fenced")


class SQLiteStateStore:
    """Same-host, multi-process reference store. Use a local filesystem, never NFS."""

    def __init__(self, path: Path, *, timeout: float = 0.1) -> None:
        if not math.isfinite(timeout) or not 0 < timeout <= 2:
            raise ValueError("store timeout must be between zero and two seconds")
        self.path = path.resolve()
        self.timeout = timeout
        with closing(self._connect()) as database, database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS state ("
                "application TEXT NOT NULL, session TEXT NOT NULL, principal TEXT NOT NULL, "
                "browser TEXT NOT NULL, schema INTEGER NOT NULL, payload BLOB NOT NULL, "
                "owner_epoch INTEGER NOT NULL, generation INTEGER NOT NULL, "
                "event_epoch TEXT NOT NULL, sequence INTEGER NOT NULL, revision INTEGER NOT NULL, "
                "PRIMARY KEY(application, session, principal, browser))"
            )

    def _connect(self) -> sqlite3.Connection:
        try:
            database = sqlite3.connect(self.path, timeout=self.timeout)
            database.execute("PRAGMA synchronous=FULL")

            return database
        except sqlite3.Error as error:
            raise StoreUnavailable("state store unavailable") from error

    @staticmethod
    def _key(identity: StateIdentity) -> tuple[str, str, str, str]:
        return identity.application, identity.session, identity.principal, identity.browser

    @staticmethod
    def _read(database: sqlite3.Connection, identity: StateIdentity) -> StateSnapshot | None:
        row = database.execute(
            "SELECT schema, payload, owner_epoch, generation, event_epoch, sequence, revision "
            "FROM state WHERE application=? AND session=? AND principal=? AND browser=?",
            SQLiteStateStore._key(identity),
        ).fetchone()

        if row is None:
            return None
        values = cast("tuple[int, bytes, int, int, str, int, int]", row)

        return StateSnapshot(identity, *values)

    def load(self, identity: StateIdentity) -> StateSnapshot | None:
        try:
            with closing(self._connect()) as database:
                return self._read(database, identity)
        except sqlite3.Error as error:
            raise StoreUnavailable("state store read failed") from error

    @staticmethod
    def _put(database: sqlite3.Connection, snapshot: StateSnapshot) -> None:
        database.execute(
            "INSERT OR REPLACE INTO state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                *SQLiteStateStore._key(snapshot.identity),
                snapshot.schema,
                snapshot.payload,
                snapshot.owner_epoch,
                snapshot.generation,
                snapshot.event_epoch,
                snapshot.sequence,
                snapshot.revision,
            ),
        )

    def _write(self, snapshot: StateSnapshot, *, claim: bool) -> StateSnapshot:
        try:
            with closing(self._connect()) as database, database:
                database.execute("BEGIN IMMEDIATE")
                current = self._read(database, snapshot.identity)

                if (current is not None or not claim) and not snapshot.matches(current):
                    raise RecoveryUnavailable("state owner has been fenced")
                committed = replace(
                    snapshot,
                    owner_epoch=snapshot.owner_epoch + int(claim),
                    generation=snapshot.generation + int(not claim),
                )
                self._put(database, committed)

            return committed
        except sqlite3.Error as error:
            raise StoreUnavailable("state store checkpoint failed") from error

    def claim(self, snapshot: StateSnapshot) -> StateSnapshot:
        return self._write(snapshot, claim=True)

    def commit(self, snapshot: StateSnapshot) -> StateSnapshot:
        return self._write(snapshot, claim=False)

    def check(self, snapshot: StateSnapshot) -> None:
        if not snapshot.matches(self.load(snapshot.identity)):
            raise RecoveryUnavailable("state owner has been fenced")


def migrate_state[T](
    snapshot: StateSnapshot,
    schema: int,
    codec: StateCodec[T],
    migrations: Mapping[int, Callable[[JSONValue], JSONValue]],
) -> T:
    if snapshot.schema > schema:
        raise IncompatibleState("stored state schema is newer than the application")
    payload = snapshot.payload
    passthrough = StateCodec[JSONValue](lambda value: value, lambda value: value, codec.max_bytes)

    for version in range(snapshot.schema, schema):
        migrate = migrations.get(version)

        if migrate is None:
            raise IncompatibleState("state schema requires an explicit migration")
        payload = passthrough.dumps(migrate(passthrough.loads(payload)))

    return codec.loads(payload)
