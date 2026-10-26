"""Local socket load, deterministic disposal counts and isolated fault evidence."""

from __future__ import annotations

import asyncio
import gc
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
import weakref
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ownership_topology import Client, Workers

from pysx import Fragment, each, native, own_effect, signal
from pysx.hosting.server import Session
from pysx.rendering.tree import apply, snapshot

if TYPE_CHECKING:
    from pysx import Signal
    from pysx.runtime.wire import JSONValue, Op


def rss_bytes(pid: int) -> int:
    return (
        int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)], text=True).strip()) * 1024
    )


def summarize(samples: list[float]) -> dict[str, JSONValue]:
    if not samples or any(not math.isfinite(value) or value < 0 for value in samples):
        raise ValueError("missing or invalid latency samples")
    ordered = sorted(samples)

    return {
        "raw_seconds": list(samples),
        "p50_seconds": ordered[math.ceil(len(samples) * 0.5) - 1],
        "p95_seconds": ordered[math.ceil(len(samples) * 0.95) - 1],
        "p99_seconds": ordered[math.ceil(len(samples) * 0.99) - 1],
    }


def churn() -> dict[str, JSONValue]:
    source = signal(0)
    references: list[weakref.ReferenceType[Session]] = []
    built = 0

    def row(_key: int) -> Fragment:
        nonlocal built
        built += 1
        own_effect("source", lambda: source())

        def click(_event: object) -> None:
            source.set(source() + 1)

        return native.Li(native.Button("click", on_click=click), css="color: red")

    def create(rows: Signal[list[int]]) -> Session:
        return Session(lambda: native.Ul(each(rows, row, key=str)))

    for _ in range(1000):
        rows = signal(list(range(10)))
        session = create(rows)
        references.append(weakref.ref(session))
        assert len(source.observers) == 10
        rows.set([])
        assert not source.observers
        session.dispose()
        assert not session.rendered.handlers
        assert not session.rendered.styles.rules
        assert not session.rendered.retired
        assert not session.effects
        assert not rows.observers
        assert not session.runtime.tasks
        del session
    gc.collect()
    retained = sum(reference() is not None for reference in references)
    assert retained == 0
    assert built == 10000

    return {
        "disposed_graphs": len(references),
        "mounted_removed_rows": built,
        "retained_graphs": retained,
        "disposed_handlers": 0,
        "disposed_observers": 0,
        "disposed_tasks": 0,
        "disposed_styles": 0,
        "retired_ids": 0,
    }


async def socket_load(directory: Path) -> dict[str, JSONValue]:
    runs: list[JSONValue] = []
    rss: list[JSONValue] = []
    clients: list[Client] = []
    with Workers(directory, count=1, workload=True) as topology:
        worker = topology.workers[0]

        try:
            for _ in range(100):
                clients.append(await Client.attach(worker))
            assert all(len(client.bindings) == 100 for client in clients)

            for run in range(5):
                samples: list[float] = []
                events: list[JSONValue] = []
                started = time.perf_counter()

                for event in range(40):
                    await asyncio.sleep(max(0, started + event / 40 - time.perf_counter()))
                    client = clients[event % 20]
                    before = time.perf_counter()

                    if event < 20:
                        await event_roundtrip(client, client.bindings[0], f"edit-{run}")
                    else:
                        await event_roundtrip(client, client.handlers[1], None)
                    seconds = time.perf_counter() - before
                    samples.append(seconds)
                    events.append(
                        {
                            "client": event % 20,
                            "sequence": client.sequence,
                            "scheduled_offset_seconds": event / 40,
                            "sent_offset_seconds": before - started,
                            "seconds": seconds,
                        }
                    )
                await asyncio.sleep(max(0, started + 1 - time.perf_counter()))
                observed = summarize(samples)
                observed["events"] = events
                observed["window_seconds"] = time.perf_counter() - started
                runs.append(observed)
                current = rss_bytes(worker.process.pid)
                rss.append(current)
                assert current <= 1024**3, current
        finally:
            await asyncio.gather(*(client.close() for client in clients))

    return {
        "sessions": len(clients),
        "rows_per_session": 100,
        "runs": runs,
        "rss_bytes": rss,
        "events_per_run": 40,
        "active_clients": 20,
        "measurement": "client event send to successful ack; includes transport/store",
        "reference_timing": "unqualified",
        "long_soak": "deferred",
    }


async def event_roundtrip(client: Client, handler: str, value: str | None) -> None:
    tree = snapshot(client.html, unwrap_row=False)
    await client.event(handler=handler, value=value, wait=False)
    async with asyncio.timeout(5):
        while True:
            frame = cast("dict[str, JSONValue]", json.loads(await client.socket.recv()))
            revision = frame.get("state_rev")

            if type(revision) is int:
                client.revision = revision
            ops = frame.get("ops")

            if isinstance(ops, list):
                apply(tree, cast("list[Op]", ops))

            if frame.get("seq") == client.sequence and frame.get("t") in ("ack", "result"):
                if frame.get("status") != "success":
                    raise RuntimeError(f"event failed: {frame}")
                client.html = tree.markup()
                client.handlers = re.findall(r'data-pysx-click="([^"]+)"', client.html)
                client.bindings = re.findall(r'data-pysx-binding="([^"]+)"', client.html)

                return


def fault_tests(directory: Path) -> dict[str, JSONValue]:
    commands = [
        [
            "tests/test_worker_ownership.py",
            "tests/test_transport.py",
            "tests/test_async_resources.py",
            "tests/test_component_lifecycle.py",
        ],
        *[
            [
                "tests/test_worker_ownership.py::test_owner_routing_fifty_sessions_per_worker_survive_owner_death"
            ]
            for _ in range(3)
        ],
    ]
    reports: list[JSONValue] = []

    for index, selection in enumerate(commands):
        target = directory / f"faults-{index}.xml"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--capture=tee-sys",
            *selection,
            f"--junitxml={target}",
        ]
        started = time.perf_counter()
        result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
        (directory / f"faults-{index}.log").write_text(result.stdout + result.stderr)

        if result.returncode != 0:
            raise RuntimeError(f"fault run {index} failed; see {directory.name}/faults-{index}.log")
        cases = list(ET.parse(target).getroot().iter("testcase"))

        if not cases or any(
            case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error")
        ):
            raise ValueError(f"fault run {index} has missing/skipped/failed cases")
        prefix = "OWNER RECOVERY SAMPLE: "
        recovery = [
            cast("dict[str, JSONValue]", json.loads(line.split(prefix, 1)[1]))
            for line in result.stdout.splitlines()
            if prefix in line
        ]

        if len(recovery) != 1:
            raise ValueError("missing owner recovery duration")
        seconds = recovery[0].get("seconds")

        if (
            recovery[0].get("sessions") != 50
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or not 0 <= seconds <= 10
        ):
            raise ValueError("owner recovery exceeded the unchanged profile bound")
        reports.append(
            {
                "selection": list(selection),
                "passed": len(cases),
                "seconds": time.perf_counter() - started,
                "junit": target.name,
                "recovery": recovery[0],
            }
        )

    return {
        "runs": reports,
        "unexpected_failures": 0,
        "skips": 0,
        "qualification": "local functional faults; repeated passes do not prove race absence",
    }


def runtime(report_directory: Path) -> dict[str, JSONValue]:
    report_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qualification-runtime-") as directory:
        try:
            load = asyncio.run(socket_load(Path(directory)))
        finally:
            for path in Path(directory).glob("*.log"):
                shutil.copyfile(path, report_directory / path.name)

    (report_directory / "load.json").write_text(json.dumps(load, indent=2) + "\n")
    counts = churn()
    (report_directory / "churn.json").write_text(json.dumps(counts, indent=2) + "\n")

    return {"load": load, "churn": counts, "faults": fault_tests(report_directory)}
