"""Diagnostic only: compare real SQLite FULL-durability write strategies."""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
import time
from contextlib import ExitStack, closing
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from pysx import SQLiteOwnershipStore, StateIdentity, StateSnapshot

JOURNALS = {
    "DELETE": "PRAGMA journal_mode=DELETE",
    "PERSIST": "PRAGMA journal_mode=PERSIST",
    "TRUNCATE": "PRAGMA journal_mode=TRUNCATE",
    "WAL": "PRAGMA journal_mode=WAL",
}


@dataclass(frozen=True)
class Strategy:
    name: str
    journal: str = "DELETE"
    retained: bool = False
    keeper: bool = False


class RetainedConnection(sqlite3.Connection):
    def close(self) -> None:
        """The benchmark owner explicitly closes this real connection at teardown."""


class ProbeStore(SQLiteOwnershipStore):
    def __init__(self, path: Path, strategy: Strategy) -> None:
        self.strategy = strategy
        self.retained: RetainedConnection | None = None
        # The clock isolates transaction throughput from lease expiry. This is
        # not a substitute for the real-time owner-recovery qualification.
        super().__init__(path, timeout=1, clock=lambda: 100.0)

    def _configure(self, database: sqlite3.Connection) -> None:
        if self.strategy.journal != "DELETE":
            database.execute(JOURNALS[self.strategy.journal])

    def _connect(self) -> sqlite3.Connection:
        if self.strategy.retained:
            if self.retained is None:
                pooled = sqlite3.connect(
                    self.path, timeout=self.timeout, factory=RetainedConnection
                )

                try:
                    pooled.execute("PRAGMA synchronous=FULL")
                    self._configure(pooled)
                except BaseException:
                    sqlite3.Connection.close(pooled)

                    raise
                self.retained = pooled

            return self.retained
        database = super()._connect()

        try:
            self._configure(database)
        except BaseException:
            database.close()

            raise

        return database

    def release(self) -> None:
        if self.retained is not None:
            sqlite3.Connection.close(self.retained)
            self.retained = None

    def validate_configuration(self) -> None:
        with closing(self._connect()) as database:
            assert database.execute("PRAGMA synchronous").fetchone() == (2,)
            assert database.execute("PRAGMA journal_mode").fetchone() == (
                self.strategy.journal.lower(),
            )


def payload(count: int) -> bytes:
    return json.dumps(
        {
            "count": count,
            "rows": [f"Row {index}" for index in range(100)],
            "order": list(range(100)),
        }
    ).encode()


def benchmark(strategy: Strategy) -> dict[str, object]:
    with TemporaryDirectory(prefix="pysx-sqlite-benchmark-") as temporary:
        path = Path(temporary) / "state.db"
        with ExitStack() as resources:
            keeper: sqlite3.Connection | None = None

            if strategy.keeper:
                keeper = resources.enter_context(closing(sqlite3.connect(path, timeout=1)))
                keeper.execute(JOURNALS[strategy.journal])
                keeper.execute("PRAGMA synchronous=FULL")
            store = ProbeStore(path, strategy)
            resources.callback(store.release)

            if keeper is not None:
                # Attach the keeper to the initialized WAL, rather than merely
                # opening the empty database before any tables exist.
                keeper.execute("SELECT count(*) FROM state").fetchone()
            snapshots = [
                store.acquire(
                    StateSnapshot(
                        StateIdentity("benchmark", f"row-{index}", "user", "browser"), 1, payload(0)
                    ),
                    "benchmark-worker",
                    30,
                )
                for index in range(100)
            ]
            store.validate_configuration()
            samples: list[float] = []

            for index, snapshot in enumerate(snapshots):
                candidate = replace(snapshot, payload=payload(index + 1))
                started = time.perf_counter()
                snapshots[index] = store.save(candidate, "benchmark-worker", 30)
                samples.append(time.perf_counter() - started)
        # Close every connection before reopening, including the WAL keeper.
        reopened = SQLiteOwnershipStore(path, timeout=1, clock=lambda: 100.0)
        assert all(reopened.load(snapshot.identity) == snapshot for snapshot in snapshots)

        return {
            "strategy": strategy.name,
            "journal": strategy.journal,
            "synchronous": "FULL",
            "rows": len(snapshots),
            "writes": len(samples),
            "total_seconds": sum(samples),
            "median_seconds": statistics.median(samples),
            "p95_seconds": sorted(samples)[94],
            "reopen_verified": True,
        }


def main() -> None:
    sys.stdout.write(
        "SQLITE STORAGE BENCHMARK ENV: "
        + json.dumps({"platform": sys.platform, "sqlite_version": sqlite3.sqlite_version})
        + "\n"
    )
    sys.stdout.flush()

    for strategy in (
        Strategy("delete/scoped"),
        Strategy("persist/scoped", "PERSIST"),
        Strategy("truncate/scoped", "TRUNCATE"),
        Strategy("wal/scoped", "WAL"),
        Strategy("wal/keeper", "WAL", keeper=True),
        Strategy("delete/retained", retained=True),
        Strategy("wal/retained", "WAL", retained=True),
    ):
        sys.stdout.write("SQLITE STORAGE BENCHMARK: " + json.dumps(benchmark(strategy)) + "\n")
        sys.stdout.flush()
    sys.stdout.write(
        "SQLITE STORAGE BENCHMARK COMPLETE: 7 strategies; not recovery qualification\n"
    )
    sys.stdout.flush()


if __name__ == "__main__":
    main()
