"""Repeatable semantic workload measurements; missing qualification never passes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import traceback
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING

from dev_workload import create_workspace, describe_workspace

from pysx import (
    Fragment,
    Route,
    Router,
    RouteState,
    Themes,
    batch,
    derived,
    each,
    native,
    own_effect,
    pysx,
    resource,
    signal,
)
from pysx.development import tracing
from pysx.hosting.server import Session

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysx import Signal
    from pysx.runtime.wire import JSONValue

ROOT = Path(__file__).resolve().parent.parent
SUITES = ("workloads", "runtime", "tooling", "all")


def graph_workload() -> dict[str, JSONValue]:
    counts: dict[str, int] = {}
    source = signal(1)

    def counted(name: str, read: Callable[[], int]) -> int:
        counts[name] = counts.get(name, 0) + 1

        return read()

    left = derived(lambda: counted("left", lambda: source() * 2))
    right = derived(lambda: counted("right", lambda: source() + 1))
    total = derived(lambda: counted("total", lambda: left() + right()))
    chain = total

    def next_link(previous: Callable[[], int], index: int) -> Signal[int]:
        return derived(lambda: counted(f"chain-{index}", lambda: previous() + 1))

    for index in range(32):
        chain = next_link(chain, index)
    seen: list[int] = []
    stop = chain.subscribe(seen.append)

    try:
        counts.clear()
        seen.clear()

        with batch():
            source.set(2)
            source.set(3)
        assert seen == [42], seen
        assert len(counts) == 35, counts
        assert set(counts.values()) == {1}, counts
        changed_counts: dict[str, JSONValue] = dict(counts)
        counts.clear()
        seen.clear()
        source.set(3)
        assert not counts
        assert not seen
    finally:
        stop()
    assert not source.observers
    state = signal({"selected": 1, "other": 0})
    selected = state["selected"]
    evaluations: list[int] = []
    value = derived(lambda: counted("selected", lambda: selected()))
    stop = value.subscribe(evaluations.append)

    try:
        counts.clear()
        evaluations.clear()
        state["other"].set(1)
        assert not counts
        assert not evaluations
    finally:
        stop()

    return {
        "affected_computations": changed_counts,
        "notifications": 1,
        "unchanged_notifications": 0,
        "unrelated_recomputations": 0,
        "chain_depth": 32,
    }


def keyed_workload() -> dict[str, JSONValue]:
    order = signal(list(range(1000)))
    fields = [signal(f"Row {index}") for index in range(1000)]
    builds: list[int] = []

    def row(index: int) -> Fragment:
        builds.append(index)

        return pysx(t"""
            li:
                input(bindValue={fields[index]})
        """)

    session = Session(lambda: pysx(t"ul: {each(order, row, key=str)}"))

    try:
        assert len(builds) == 1000
        builds.clear()
        fields[500].set("edited")
        edits = list(session.pending)
        assert not builds
        assert 1 <= len(edits) <= 3
        assert all(op["op"] in ("text", "attr", "prop") for op in edits)
        session.pending.clear()
        order.set(list(reversed(order())))
        moves = list(session.pending)
        assert not builds
        assert len(moves) == 1
        assert moves[0]["op"] == "list"
        assert moves[0].get("html") == {}
    finally:
        session.dispose()
    assert not order.observers
    assert all(not field.observers for field in fields)

    return {
        "rows": 1000,
        "edit_ops": len(edits),
        "edit_row_evaluations": 0,
        "reorder_ops": len(moves),
        "reorder_html_replacements": 0,
        "disposed_observers": 0,
        "dom_identity": "requires browser observation",
    }


def workloads() -> dict[str, JSONValue]:
    with tempfile.TemporaryDirectory(prefix="qualification-workspace-") as directory:
        workspace = Path(directory)
        create_workspace(workspace)
        editor = describe_workspace(workspace)
    assert (editor["files"], editor["components"], editor["bytes"], editor["largest_bytes"]) == (
        200,
        500,
        1048576,
        102400,
    )

    return {
        "graph": graph_workload(),
        "keyed": keyed_workload(),
        "editor_workspace": editor,
        "sessions": asyncio.run(session_workload()),
        "inspector": inspector_workload(),
    }


async def session_workload() -> dict[str, JSONValue]:
    states = [signal(0) for _ in range(100)]
    sessions: list[Session] = []

    def app(index: int) -> Fragment:
        state = states[index]
        fields = [signal(f"Row {row}") for row in range(100)]
        order = signal(list(range(100)))
        themes = Themes()
        themes.register("light", {"ink": "#123456"})
        themes.register("dark", {"ink": "#abcdef"})
        own_effect("theme", lambda: themes.select("dark" if state() % 2 else "light"))

        async def load() -> str:
            await asyncio.sleep(0)

            return "ready"

        loaded = resource("load", load)

        def page(route: RouteState) -> Fragment:
            return native.Main(
                native.P(state),
                native.P(derived(lambda: loaded().status)),
                native.P(derived(lambda: route.location().path)),
                native.Ul(
                    each(
                        order, lambda row: native.Li(native.Input(bind_value=fields[row])), key=str
                    )
                ),
            )

        router = Router([Route("*", page)])

        return pysx(t"{router.view()}", themes=themes)

    try:
        def create(index: int) -> Session:
            return Session(lambda: app(index))

        for index in range(100):
            sessions.append(create(index))
        # Wait for actual resource completion, with a bounded deadline.
        async with asyncio.timeout(10):
            while any(session.runtime.tasks for session in sessions):
                await asyncio.sleep(0.01)

        for session in sessions:
            session.pending.clear()
        started = time.perf_counter()
        samples: list[JSONValue] = []

        # Twenty active clients at two events/second for one local second.
        for tick in range(2):
            for index in range(20):
                await asyncio.sleep(max(0, started + tick * 0.5 + index / 40 - time.perf_counter()))
                before = time.perf_counter()
                with sessions[index].context():
                    states[index].set(tick + 1)
                assert sessions[index].pending
                samples.append(time.perf_counter() - before)
                sessions[index].pending.clear()
        assert all(not session.pending for session in sessions[20:])
        handlers = sum(len(session.rendered.handlers) for session in sessions)
        effects = sum(len(session.effects) for session in sessions)
    finally:
        for session in sessions:
            session.dispose()
        await asyncio.sleep(0)
    assert all(not state.observers for state in states)
    assert all(not session.runtime.tasks for session in sessions)

    return {
        "graphs": len(sessions),
        "rows_per_graph": 100,
        "active_clients": 20,
        "events_per_second": 40,
        "event_count": len(samples),
        "mutation_seconds": samples,
        "handlers": handlers,
        "effects": effects,
        "disposed_tasks": 0,
        "disposed_observers": 0,
        "transport": "in-process graphs; socket attachment qualified separately",
    }


def inspector_workload() -> dict[str, JSONValue]:
    enabled, roots = tracing.enabled, tracing.roots

    try:
        tracing.configure(enable=True)
        trace = tracing.Trace()

        for _ in range(1024):
            trace.record("signal.write")
        assert len(trace.events) == 256
        assert trace.dropped == 768
        assert all(event["value"] == "[redacted]" for event in trace.events)
    finally:
        tracing.configure(enable=enabled, source_roots=roots)

    return {"recorded": 1024, "retained": len(trace.events), "dropped": trace.dropped}


def validate_workloads(metrics: dict[str, JSONValue]) -> None:
    required = {
        "graph": {"affected_computations", "notifications", "unrelated_recomputations"},
        "keyed": {"rows", "edit_ops", "reorder_html_replacements", "disposed_observers"},
        "editor_workspace": {"files", "components", "bytes", "largest_bytes", "sha256"},
        "sessions": {"graphs", "event_count", "mutation_seconds", "disposed_tasks"},
        "inspector": {"recorded", "retained", "dropped"},
    }

    for group, names in required.items():
        values = metrics.get(group)

        if not isinstance(values, dict) or not names <= values.keys():
            raise ValueError(f"missing required {group} metrics")
    sessions = metrics["sessions"]
    assert isinstance(sessions, dict)
    samples = sessions["mutation_seconds"]

    if not isinstance(samples, list) or len(samples) != 40:
        raise ValueError("expected all 40 mutation samples")

    if any(
        not isinstance(sample, (int, float)) or isinstance(sample, bool) or sample < 0
        for sample in samples
    ):
        raise ValueError("invalid mutation sample")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, choices=SUITES)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    # Never leave an earlier successful report at the requested output on failure.
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, JSONValue] = {
        "suite": args.suite,
        "status": "failed",
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "scope": "available-machine local checks; reference numeric qualification deferred",
            "dependencies": {
                name: version(name)
                for name in ("websockets", "cryptography", "pygls", "pyright", "ruff")
            },
            "candidate_sources": {
                str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in [
                    *ROOT.glob("tests/qualification*.py"),
                    *ROOT.glob("tests/browser_qualification*"),
                    ROOT / "tests/browser_runner.mjs",
                    ROOT / "editor/test/runner.mjs",
                    ROOT / "editor/test/suite.cjs",
                    ROOT / "tests/test_qualification.py",
                    ROOT / "tests/ownership_topology.py",
                    ROOT / "tests/test_worker_ownership.py",
                    ROOT / "tests/recovery_workload_fixture.py",
                    ROOT / "pysx/hosting/durable_host.py",
                    ROOT / "docs/OWNERSHIP.md",
                ]
            },
        },
        "metrics": {},
        "reference_qualified": False,
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    started = time.perf_counter()

    try:
        if args.suite == "all":
            metrics: dict[str, JSONValue] = {}
            report["metrics"] = metrics

            for suite in SUITES[:-1]:
                suite_started = time.perf_counter()
                metrics[suite] = measure(suite, args.report.parent)
                child = {
                    **report,
                    "suite": suite,
                    "status": "passed",
                    "metrics": metrics[suite],
                    "elapsed_seconds": time.perf_counter() - suite_started,
                }
                (args.report.parent / f"{suite}.json").write_text(
                    json.dumps(child, indent=2) + "\n"
                )
        else:
            metrics = measure(args.suite, args.report.parent)
        report["metrics"] = metrics
        report["status"] = "passed"
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        report["traceback"] = traceback.format_exc()
    report["elapsed_seconds"] = time.perf_counter() - started
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    passed = report["status"] == "passed"
    sys.stdout.write(f"QUALIFICATION {'PASSED' if passed else 'FAILED'}: {args.suite}\n")

    return 0 if passed else 1


def measure(suite: str, directory: Path) -> dict[str, JSONValue]:
    if suite == "workloads":
        metrics = workloads()
        validate_workloads(metrics)

        return metrics

    if suite == "runtime":
        from qualification_runtime import runtime

        metrics = runtime(directory / "runtime-details")
        validate_groups(metrics, {"load", "churn", "faults"})

        return metrics

    if suite == "tooling":
        from qualification_tooling import tooling

        metrics = tooling(directory / "tooling-details")
        validate_groups(metrics, {"browsers", "resident", "disabled_overhead", "development"})

        return metrics

    raise ValueError(f"unknown measurement suite: {suite}")


def validate_groups(metrics: dict[str, JSONValue], groups: set[str]) -> None:
    if any(
        not isinstance(metrics.get(group), (dict, list)) or not metrics[group] for group in groups
    ):
        raise ValueError("missing required measurement groups")


if __name__ == "__main__":
    raise SystemExit(main())
