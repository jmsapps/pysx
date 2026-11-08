"""Fixed recovery workload: keyed forms, routes, themes and owned async work."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field, replace
from http.cookies import SimpleCookie
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pysx import (
    DurableApplication,
    Fragment,
    HostConfig,
    Route,
    Router,
    RouteState,
    Signal,
    SQLiteOwnershipStore,
    StateCodec,
    Themes,
    TransportLimits,
    derived,
    each,
    native,
    own_effect,
    project,
    pysx,
    resource,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from pysx.hosting.request import RequestContext
    from pysx.runtime.persistence import StateSnapshot
    from pysx.runtime.wire import JSONValue


class ObservedOwnershipStore(SQLiteOwnershipStore):
    """Record failed validation metadata without logging state or identities."""

    def __init__(self, path: Path) -> None:
        self.observed_errors: set[tuple[str, str, str]] = set()
        self.transaction_counts: dict[str, int] = {}
        self.transaction_seconds: dict[str, float] = {}
        self.slow_reports: dict[str, int] = {}
        # Shared CI disks can hold a competing transaction longer than the
        # default 100ms. Keep the configured wait finite and below the 3s lease.
        super().__init__(path, timeout=1)

    def record_failure(self, operation: str, error: Exception) -> None:
        cause: BaseException = error

        while cause.__cause__ is not None:
            cause = cause.__cause__
        key = (operation, type(error).__name__, type(cause).__name__)

        if key in self.observed_errors:
            return
        self.observed_errors.add(key)
        sys.stderr.write(
            "OWNERSHIP STORE FAILURE: "
            + json.dumps(
                {
                    "operation": operation,
                    "error_type": key[1],
                    "cause_type": key[2],
                    "sqlite_errorname": str(getattr(cause, "sqlite_errorname", "none")),
                    "transaction_count": self.transaction_counts.get(operation, 0),
                    "transaction_seconds": self.transaction_seconds.get(operation, 0),
                }
            )
            + "\n"
        )
        sys.stderr.flush()

    def _connect(self) -> sqlite3.Connection:
        try:
            return super()._connect()
        except Exception as error:
            self.record_failure("connect", error)

            raise

    def _transaction[T](self, callback: Callable[[sqlite3.Connection], T]) -> T:
        operation = str(getattr(callback, "__qualname__", "transaction"))
        started = time.monotonic()
        self.transaction_counts[operation] = self.transaction_counts.get(operation, 0) + 1

        try:
            return super()._transaction(callback)
        except Exception as error:
            self.record_failure(operation, error)

            raise
        finally:
            elapsed = time.monotonic() - started
            self.transaction_seconds[operation] = (
                self.transaction_seconds.get(operation, 0) + elapsed
            )

            if elapsed >= 0.25 and self.slow_reports.get(operation, 0) < 3:
                self.slow_reports[operation] = self.slow_reports.get(operation, 0) + 1
                sys.stderr.write(
                    "OWNERSHIP TRANSACTION: "
                    + json.dumps(
                        {
                            "operation": operation,
                            "elapsed_seconds": elapsed,
                            "transaction_count": self.transaction_counts[operation],
                            "transaction_seconds": self.transaction_seconds[operation],
                        }
                    )
                    + "\n"
                )
                sys.stderr.flush()

    def _valid(
        self, database: sqlite3.Connection, snapshot: StateSnapshot, worker: str, now: float
    ) -> bool:
        valid = super()._valid(database, snapshot, worker, now)

        if not valid:
            lease = self._lease(database, snapshot.identity)
            stored = self._read(database, snapshot.identity)
            sys.stderr.write(
                "OWNERSHIP VALIDATION: "
                + json.dumps(
                    {
                        "lease_present": lease is not None,
                        "lease_remaining_seconds": lease.expires - now
                        if lease is not None
                        else None,
                        "worker_matches": lease.worker == worker if lease is not None else False,
                        "lease_epoch_matches": lease.epoch == snapshot.owner_epoch
                        if lease is not None
                        else False,
                        "state_epoch_matches": stored.owner_epoch == snapshot.owner_epoch
                        if stored is not None
                        else False,
                        "generation_matches": stored.generation == snapshot.generation
                        if stored is not None
                        else False,
                    }
                )
                + "\n"
            )

        return valid


def principal(request: RequestContext) -> str:
    cookies = SimpleCookie()
    cookies.load(request.headers.get("cookie", ""))
    user = cookies.get("fixture_user")

    return user.value if user is not None else "alice"


@dataclass(frozen=True)
class WorkloadState:
    count: int = 0
    rows: tuple[str, ...] = field(default_factory=lambda: tuple(f"Row {i}" for i in range(100)))
    order: tuple[int, ...] = field(default_factory=lambda: tuple(range(100)))
    theme: str = "light"
    location: str = ""


def encode(state: WorkloadState) -> JSONValue:
    rows: list[JSONValue] = list(state.rows)
    order: list[JSONValue] = list(state.order)

    return {
        "count": state.count,
        "rows": rows,
        "order": order,
        "theme": state.theme,
        "location": state.location,
    }


def decode(value: JSONValue) -> WorkloadState:
    if not isinstance(value, dict):
        raise ValueError("expected workload object")
    count, rows, order = value.get("count"), value.get("rows"), value.get("order")
    theme, location = value.get("theme"), value.get("location")

    if type(count) is not int or not isinstance(rows, list) or not isinstance(order, list):
        raise ValueError("invalid workload counters or rows")

    if len(rows) != 100 or len(order) != 100:
        raise ValueError("workload requires 100 rows")
    texts: list[str] = []
    keys: list[int] = []

    for text in rows:
        if not isinstance(text, str) or len(text) > 128:
            raise ValueError("invalid row value")
        texts.append(text)

    for key in order:
        if type(key) is not int:
            raise ValueError("invalid row key")
        keys.append(key)

    if set(keys) != set(range(100)):
        raise ValueError("invalid row permutation")

    if not isinstance(theme, str) or theme not in ("light", "dark"):
        raise ValueError("invalid workload theme")

    if not isinstance(location, str) or (location and not location.startswith("/")):
        raise ValueError("invalid workload location")

    return WorkloadState(count, tuple(texts), tuple(keys), theme, location)


def app(state: Signal[WorkloadState]) -> Fragment:
    generation = uuid4().hex
    themes = Themes()
    themes.register("light", {"ink": "#123456"})
    themes.register("dark", {"ink": "#abcdef"})
    own_effect("theme", lambda: themes.select(state().theme))

    async def load() -> str:
        await asyncio.sleep(0.02)

        return "ready"

    loading = resource("workload", load)
    status = derived(lambda: loading().status)
    count = derived(lambda: state().count)
    order = derived(lambda: state().order)

    def row(index: int) -> Fragment:
        value = project(
            state,
            lambda data: data.rows[index],
            lambda data, text: replace(
                data, rows=(*data.rows[:index], text[:128], *data.rows[index + 1 :])
            ),
        )

        return native.Li(
            native.Input(bind_value=value, id=f"workload-field-{index}"),
            id=f"workload-row-{index}",
        )

    def increment(_value: object) -> None:
        data = state()
        state.set(replace(data, count=data.count + 1))

    def reorder(_value: object) -> None:
        data = state()
        state.set(
            replace(
                data,
                order=tuple(reversed(data.order)),
                theme="dark",
                location="/workload/reordered",
            )
        )
        router.navigate("/workload/reordered")

    def page(route: RouteState) -> Fragment:
        return native.Main(
            native.H1("Durable counter"),
            native.Span(count, id="durable-count"),
            native.Button("Increment", id="durable-increment", on_click=increment),
            native.Button("Reorder", id="durable-reorder", on_click=reorder),
            native.Span(generation, id="durable-generation"),
            native.Span(status, id="workload-resource"),
            native.Span(derived(lambda: route.location().path), id="workload-path"),
            native.Ul(each(order, row, key=str), id="workload-rows"),
            css="color: var(--ink)",
        )

    router = Router([Route("/workload/:view", page), Route("*", page)])
    data = state()

    if data.location and data.location != router.location().url:
        router.navigate(data.location, replace=True)
    elif not data.location:
        state.set(replace(data, location=router.location().url))

    return pysx(t"{router.view()}", themes=themes)


host = DurableApplication(
    app,
    initial=WorkloadState,
    codec=StateCodec(encode, decode),
    store=ObservedOwnershipStore(Path(os.environ["PYSX_FIXTURE_STORE"])),
    application="worker-counter",
    worker=os.environ["PYSX_FIXTURE_WORKER"],
    lease_seconds=float(os.environ.get("PYSX_FIXTURE_LEASE", "3")),
    schema=int(os.environ.get("PYSX_FIXTURE_SCHEMA", "1")),
    migrations={1: lambda value: value} if os.environ.get("PYSX_FIXTURE_MIGRATE") else {},
    fresh_on_incompatible=os.environ.get("PYSX_FIXTURE_FRESH") == "1",
    config=HostConfig(principal=principal, transport=TransportLimits(graphs=100, detached=50)),
)
