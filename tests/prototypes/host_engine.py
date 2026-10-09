"""Isolated HTTP/adoption/state-engine proof, not a production host.

Authentication is injected fixture context. The store is an in-memory atomic
contract adapter, not a distributed-store/topology qualification. Only declared
frozen app state is serialized; graph callbacks and live resources are rebuilt.
"""

from __future__ import annotations

import asyncio
import html
import json
import secrets
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Required, TypedDict, cast

from pysx.reactive import Effect, Signal, effect, signal

if TYPE_CHECKING:
    from collections.abc import Callable


APP_ID = "pysx-host-proof"
MAX_STATE_BYTES = 4096
CSS = ".proof-count { color: rgb(20, 70, 120); font-weight: 700; }"


@dataclass(frozen=True)
class AppState:
    count: int = 0
    text: str = "server"


@dataclass(frozen=True)
class Snapshot:
    session: str
    principal: str
    revision: int
    highwater: int
    state: AppState
    version: int = 1
    app: str = APP_ID


def encode(snapshot: Snapshot) -> bytes:
    data = {
        "version": snapshot.version,
        "app": snapshot.app,
        "session": snapshot.session,
        "principal": snapshot.principal,
        "revision": snapshot.revision,
        "highwater": snapshot.highwater,
        "state": {"count": snapshot.state.count, "text": snapshot.state.text},
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    if len(payload) > MAX_STATE_BYTES:
        raise ValueError("state exceeds explicit codec byte limit")
    # Symmetric validation prevents invalid Python bool/int state entering storage.
    decode(payload)

    return payload


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")

    return value


def _string(value: object, label: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 1000 or (nonempty and not value):
        raise ValueError(f"invalid {label}")

    return value


def decode(payload: bytes) -> Snapshot:
    if len(payload) > MAX_STATE_BYTES:
        raise ValueError("state exceeds explicit codec byte limit")
    decoded: object = json.loads(payload)

    if not isinstance(decoded, dict):
        raise ValueError("snapshot must be an object")
    data = cast("dict[str, object]", decoded)
    expected = {"version", "app", "session", "principal", "revision", "highwater", "state"}

    if set(data) != expected or data.get("app") != APP_ID:
        raise ValueError("incompatible snapshot envelope")
    version = _integer(data["version"], "version")

    if version not in {0, 1}:
        raise ValueError("unsupported snapshot version")
    state = data["state"]

    if not isinstance(state, dict):
        raise ValueError("state must be an object")
    values = cast("dict[str, object]", state)
    required = {"count"} if version == 0 else {"count", "text"}

    if set(values) != required:
        raise ValueError("incompatible state fields")

    return Snapshot(
        _string(data["session"], "session", nonempty=True),
        _string(data["principal"], "principal", nonempty=True),
        _integer(data["revision"], "revision"),
        _integer(data["highwater"], "highwater"),
        AppState(_integer(values["count"], "count"), _string(values.get("text", "server"), "text")),
    )


@dataclass(frozen=True)
class Lease:
    session: str
    owner: str
    epoch: int


@dataclass(frozen=True)
class Stored:
    payload: bytes
    lease: Lease


class StaleOwnerError(ValueError):
    pass


class Store:
    """Atomic compare/commit operations have no await or partial mutation path."""

    def __init__(self) -> None:
        self.records: dict[str, Stored] = {}
        self.fail_next_commit = False

    def create(self, snapshot: Snapshot, owner: str) -> Lease:
        if snapshot.session in self.records:
            raise ValueError("session already exists")
        lease = Lease(snapshot.session, owner, 1)
        self.records[snapshot.session] = Stored(encode(snapshot), lease)

        return lease

    def check(self, lease: Lease) -> Stored:
        record = self.records.get(lease.session)

        if record is None or record.lease != lease:
            raise StaleOwnerError("stale owner epoch")

        return record

    def commit(self, lease: Lease, candidate: Snapshot, *, expected_revision: int) -> None:
        record = self.check(lease)
        current = decode(record.payload)

        if (
            candidate.session != lease.session
            or candidate.principal != current.principal
            or current.revision != expected_revision
            or candidate.revision != expected_revision + 1
            or candidate.highwater < current.highwater
            or candidate.highwater > current.highwater + 1
        ):
            raise ValueError("snapshot compare-and-commit rejected")
        payload = encode(candidate)

        if self.fail_next_commit:
            self.fail_next_commit = False

            raise OSError("injected checkpoint failure")
        self.records[lease.session] = Stored(payload, lease)

    def claim(self, session: str, owner: str, *, expected_epoch: int) -> Lease:
        current = self.records[session]

        if current.lease.epoch != expected_epoch:
            raise StaleOwnerError("concurrent owner claim lost")
        lease = Lease(session, owner, expected_epoch + 1)
        self.records[session] = Stored(current.payload, lease)

        return lease


class Graph:
    """Actual Signal/effect graph with fresh handler closure on reconstruction."""

    def __init__(self, initial: AppState, serial: int) -> None:
        self.state: Signal[AppState] = signal(initial)
        self.serial = serial
        self.disposals = 0
        self.observed: list[AppState] = []
        self.watcher: Effect = effect(lambda: self.observed.append(self.state.get()))

        def handler(name: str, value: str) -> AppState:
            if self.disposals:
                raise ValueError("disposed graph")
            current = self.state.get()

            if name == "increment":
                return replace(current, count=current.count + 1)

            if name == "edit":
                return replace(current, text=value)

            raise ValueError("unknown handler")

        self.handler: Callable[[str, str], AppState] = handler

    def dispose(self) -> None:
        if not self.disposals:
            self.watcher.dispose()
            self.disposals += 1


@dataclass
class Session:
    snapshot: Snapshot
    lease: Lease
    graph: Graph
    expires: float
    mutation: asyncio.Lock
    adopted: bool = False


class TextOp(TypedDict):
    op: str
    id: str
    value: str


class Reply(TypedDict, total=False):
    t: Required[str]
    session: str
    epoch: int
    revision: int
    setup_serial: int
    event: int
    ops: list[TextOp]
    code: str
    edited_text: str


@dataclass(frozen=True)
class HTTPResult:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    token: str = ""


class Engine:
    def __init__(
        self,
        *,
        owner: str = "worker-a",
        store: Store | None = None,
        capacity: int = 8,
        ttl: float = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity <= 0 or ttl <= 0:
            raise ValueError("adoption capacity and TTL must be positive")
        self.owner = owner
        self.store = store if store is not None else Store()
        self.capacity = capacity
        self.ttl = ttl
        self.clock = clock
        self.lock = asyncio.Lock()
        self.pending: dict[str, Session] = {}
        self.active: dict[str, Session] = {}
        self.setup_count = 0

    def _graph(self, state: AppState) -> Graph:
        self.setup_count += 1

        return Graph(state, self.setup_count)

    def expire(self) -> None:
        now = self.clock()

        for token, session in list(self.pending.items()):
            if session.expires <= now:
                self.pending.pop(token)
                session.graph.dispose()
                record = self.store.records.get(session.snapshot.session)

                if record is not None and record.lease == session.lease:
                    self.store.records.pop(session.snapshot.session)

    async def http(self, principal: str) -> HTTPResult:
        _string(principal, "principal", nonempty=True)
        async with self.lock:
            self.expire()

            if len(self.pending) + len(self.active) >= self.capacity:
                return HTTPResult(503, (("cache-control", "no-store"),), b"admission full")
            identifier = secrets.token_urlsafe(18)
            token = secrets.token_urlsafe(24)
            snapshot = Snapshot(identifier, principal, 0, 0, AppState())
            lease = self.store.create(snapshot, self.owner)
            graph = self._graph(snapshot.state)
            session = Session(snapshot, lease, graph, self.clock() + self.ttl, asyncio.Lock())
            self.pending[token] = session
            state = graph.state.get()
            body = (
                '<!doctype html><html><head><meta charset="utf-8">'
                f'<style id="proof-style">{CSS}</style></head><body>'
                f'<main id="proof-root" data-adoption="{html.escape(token, quote=True)}" '
                f'data-setup-serial="{graph.serial}">'
                "<h1>Server rendered proof</h1>"
                f'<span id="proof-count" class="proof-count">{state.count}</span>'
                f'<input id="proof-input" value="{html.escape(state.text, quote=True)}">'
                '<button id="proof-increment">Increment</button></main>'
                '<script src="/client.js" defer></script></body></html>'
            ).encode()

            return HTTPResult(
                200,
                (
                    ("content-type", "text/html; charset=utf-8"),
                    ("cache-control", "private, no-store"),
                ),
                body,
                token,
            )

    async def adopt(self, token: str, principal: str, edited_text: str) -> tuple[Session, Reply]:
        _string(edited_text, "edited input")
        async with self.lock:
            self.expire()
            session = self.pending.get(token)

            if session is None or session.snapshot.principal != principal:
                raise ValueError("unknown or principal-mismatched adoption token")
            self.store.check(session.lease)
            # Commit reconciled browser edits before acknowledgement; never replace DOM.
            candidate = replace(
                session.snapshot,
                revision=session.snapshot.revision + 1,
                state=replace(session.snapshot.state, text=edited_text),
            )
            self.store.commit(session.lease, candidate, expected_revision=session.snapshot.revision)
            session.snapshot = candidate
            session.graph.state.set(candidate.state)
            session.adopted = True
            self.pending.pop(token)
            self.active[candidate.session] = session

            return session, {
                "t": "adopted",
                "session": candidate.session,
                "epoch": session.lease.epoch,
                "revision": candidate.revision,
                "setup_serial": session.graph.serial,
                "edited_text": candidate.state.text,
            }

    async def dispatch(
        self,
        session: Session,
        *,
        epoch: int,
        event: int,
        name: str,
        value: str,
    ) -> Reply:
        _integer(epoch, "epoch")
        _integer(event, "event")
        _string(value, "event value")
        async with session.mutation:
            self.store.check(session.lease)

            if not session.adopted or epoch != session.lease.epoch:
                raise StaleOwnerError("event owner epoch mismatch")

            if event <= session.snapshot.highwater:
                return {"t": "duplicate", "event": event, "revision": session.snapshot.revision}

            if event != session.snapshot.highwater + 1:
                raise ValueError("event high-water gap")
            candidate_state = session.graph.handler(name, value)
            candidate = replace(
                session.snapshot,
                revision=session.snapshot.revision + 1,
                highwater=event,
                state=candidate_state,
            )
            # No graph mutation, observation, visible patch or ACK precedes this checkpoint.
            self.store.commit(session.lease, candidate, expected_revision=session.snapshot.revision)
            session.snapshot = candidate
            session.graph.state.set(candidate_state)

            return {
                "t": "patch",
                "event": event,
                "revision": candidate.revision,
                "ops": [{"op": "text", "id": "proof-count", "value": str(candidate_state.count)}],
            }

    async def recover(self, identifier: str, principal: str, *, expected_epoch: int) -> Session:
        async with self.lock:
            self.expire()
            previous = self.active.get(identifier)

            if previous is None and len(self.pending) + len(self.active) >= self.capacity:
                raise ValueError("recovery admission full")
            record = self.store.records[identifier]
            snapshot = decode(record.payload)

            if snapshot.principal != principal:
                raise ValueError("recovery principal mismatch")
            lease = self.store.claim(identifier, self.owner, expected_epoch=expected_epoch)

            if previous is not None:
                previous.graph.dispose()
            session = Session(snapshot, lease, self._graph(snapshot.state), 0, asyncio.Lock(), True)
            self.active[identifier] = session

            return session

    async def close(self, session: Session) -> None:
        session.graph.dispose()

        if self.active.get(session.snapshot.session) is session:
            self.active.pop(session.snapshot.session)

    async def shutdown(self) -> None:
        for session in (*self.pending.values(), *self.active.values()):
            session.graph.dispose()
        self.pending.clear()
        self.active.clear()

    async def message(
        self,
        raw: str,
        principal: str,
        session: Session | None,
    ) -> tuple[Session | None, Reply]:
        if len(raw.encode("utf-8")) > MAX_STATE_BYTES:
            raise ValueError("message exceeds proof byte limit")
        decoded: object = json.loads(raw)

        if not isinstance(decoded, dict):
            raise ValueError("message must be an object")
        message = cast("dict[str, object]", decoded)

        if message.get("t") == "hello" and session is None:
            if set(message) != {"t", "token", "edits"}:
                raise ValueError("invalid adoption shape")
            edits = message["edits"]

            if not isinstance(edits, dict) or set(cast("dict[str, object]", edits)) != {"input"}:
                raise ValueError("invalid edited-control snapshot")
            values = cast("dict[str, object]", edits)

            return await self.adopt(
                _string(message["token"], "token", nonempty=True),
                principal,
                _string(values["input"], "edited input"),
            )

        if message.get("t") == "event" and session is not None:
            if set(message) != {"t", "epoch", "event", "name", "value"}:
                raise ValueError("invalid event shape")

            if session.snapshot.principal != principal:
                raise ValueError("event principal mismatch")
            reply = await self.dispatch(
                session,
                epoch=_integer(message["epoch"], "epoch"),
                event=_integer(message["event"], "event"),
                name=_string(message["name"], "handler", nonempty=True),
                value=_string(message["value"], "event value"),
            )

            return session, reply

        raise ValueError("invalid message phase")
