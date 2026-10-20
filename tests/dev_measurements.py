"""Focused development timings; final reference load qualification is separate."""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import http.client
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import statistics
import sys
import tempfile
import time
from contextlib import chdir
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, cast

from dev_workload import create_workspace, describe_workspace
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.typing import Origin

import pysx.reactive as reactive
from pysx import tracing
from pysx.development import Supervisor, WatchConfig

PROJECT = Path(__file__).resolve().parents[1]

if TYPE_CHECKING:
    from collections.abc import Callable

    from websockets.asyncio.client import ClientConnection


class Observer(Protocol):
    def dispose(self) -> None: ...


class ReloadSample(TypedDict):
    seconds: float
    states: list[int]
    workers: int
    error: str | None


class Value(Protocol):
    computation: Observer | None
    observers: dict[int, object]

    def __call__(self) -> int: ...
    def set(self, value: int) -> None: ...


class WithoutTraceGuards(ast.NodeTransformer):
    removed: int = 0

    def visit_If(self, node: ast.If) -> ast.AST | None:
        test = node.test

        if isinstance(test, ast.Name) and test.id == "trace_enabled":
            if (
                node.orelse
                or len(node.body) != 1
                or not isinstance(node.body[0], ast.Expr)
                or not isinstance(node.body[0].value, ast.Call)
                or not isinstance(node.body[0].value.func, ast.Attribute)
                or node.body[0].value.func.attr != "emit"
                or not isinstance(node.body[0].value.func.value, ast.Name)
                or node.body[0].value.func.value.id != "_tracing"
            ):
                raise ValueError("baseline would remove non-tracing behavior")
            self.removed += 1

            return None

        return self.generic_visit(node)


class WithoutCaptureHook(ast.NodeTransformer):
    removed: int = 0

    def visit_If(self, node: ast.If) -> ast.AST | None:
        test = node.test

        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "created"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.IsNot)
        ):
            self.removed += 1

            return None

        return self.generic_visit(node)


def construction_work(engine: ModuleType, iterations: int) -> float:
    factory = cast("Callable[[int], Value]", engine.signal)
    started = time.perf_counter()

    for _ in range(iterations):
        factory(0)

    return time.perf_counter() - started


def capture_overhead() -> dict[str, object]:
    path = Path(reactive.__file__ or "")
    source = path.read_text()
    transform = WithoutCaptureHook()
    tree = transform.visit(ast.parse(source))
    assert transform.removed == 1
    baseline = ModuleType("pysx._measurement_capture_baseline")
    baseline.__package__ = "pysx"
    baseline.__file__ = str(path)
    sys.modules[baseline.__name__] = baseline

    try:
        exec(compile(tree, str(path), "exec"), baseline.__dict__)
        construction_work(reactive, 50000)
        construction_work(baseline, 50000)
        samples: list[dict[str, float]] = []

        for run in range(5):
            if run % 2:
                disabled = construction_work(reactive, 200000)
                unhooked = construction_work(baseline, 200000)
            else:
                unhooked = construction_work(baseline, 200000)
                disabled = construction_work(reactive, 200000)
            samples.append(
                {
                    "unhooked_seconds": unhooked,
                    "disabled_seconds": disabled,
                    "overhead_percent": 100 * (disabled / unhooked - 1),
                }
            )
    finally:
        sys.modules.pop(baseline.__name__, None)

    return {
        "samples": samples,
        "iterations_per_sample": 200000,
        "warmup_iterations": 50000,
        "baseline": "same source with the disabled capture hook removed",
        "measures": "signal construction, where the capture hook lives",
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "limit_percent": 5,
        "passed": all(sample["overhead_percent"] <= 5 for sample in samples),
    }


def preservation_scale() -> dict[str, object]:
    from pysx import native, signal
    from pysx.development_control import Control
    from pysx.hosting import Application, HostConfig
    from pysx.lifecycle import capture_local_state
    from pysx.request import RequestContext
    from pysx.session import Session

    def app() -> object:
        return native.Main(*[native.P(signal(index), id=f"s{index}") for index in range(64)])

    capture_local_state(enable=True)
    application = Application(cast("Any", app), config=HostConfig(development=True))
    sessions: list[Session] = []

    try:
        for index in range(32):
            request = RequestContext(path="/", headers={"cookie": f"pysx_browser=b{index}"})
            session = Session(cast("Any", app), development=True, request=request)
            application.runtime.graphs.add(session)
            application.runtime.attach(session, request)
            sessions.append(session)
        control = Control(application)
        control.freeze_local()
        started = time.perf_counter()
        report = cast("dict[str, object]", control.freeze_local())
        elapsed = time.perf_counter() - started
    finally:
        for session in sessions:
            session.dispose()
        capture_local_state(enable=False)

    return {
        "sessions": 32,
        "signals_per_session": 64,
        "freeze_seconds": elapsed,
        "snapshot_bytes": report.get("bytes"),
        "bounded": report.get("bounded"),
        "rows": len(cast("list[object]", report["rows"])),
    }


def graph_work(engine: ModuleType, iterations: int) -> float:
    signal = cast("Callable[[int], Value]", engine.signal)
    derived = cast("Callable[[Callable[[], int]], Value]", engine.derived)
    observe = cast("Callable[[Callable[[], object]], Observer]", engine.Effect)
    started = time.perf_counter()
    value = signal(0)
    computed = derived(lambda: value() * 2)
    observed: list[int] = []
    observer = observe(lambda: observed.append(computed()))

    for number in range(1, iterations + 1):
        value.set(number)
    observer.dispose()
    assert computed.computation is not None
    computed.computation.dispose()
    elapsed = time.perf_counter() - started
    assert observed == [number * 2 for number in range(iterations + 1)]
    assert not value.observers
    assert not computed.observers

    return elapsed


def disabled_overhead() -> dict[str, object]:
    tracing.configure(enable=False)
    path = Path(reactive.__file__ or "")
    source = path.read_text()
    transform = WithoutTraceGuards()
    tree = transform.visit(ast.parse(source))
    assert transform.removed == 3
    baseline = ModuleType("pysx._measurement_baseline")
    baseline.__package__ = "pysx"
    baseline.__file__ = str(path)
    sys.modules[baseline.__name__] = baseline

    try:
        exec(compile(tree, str(path), "exec"), baseline.__dict__)
        graph_work(reactive, 100000)
        graph_work(baseline, 100000)
        samples: list[dict[str, float]] = []

        for run in range(5):
            if run % 2:
                disabled = graph_work(reactive, 250000)
                unhooked = graph_work(baseline, 250000)
            else:
                unhooked = graph_work(baseline, 250000)
                disabled = graph_work(reactive, 250000)
            samples.append(
                {
                    "unhooked_seconds": unhooked,
                    "disabled_seconds": disabled,
                    "overhead_percent": 100 * (disabled / unhooked - 1),
                }
            )
    finally:
        sys.modules.pop(baseline.__name__, None)

    return {
        "samples": samples,
        "iterations_per_sample": 250000,
        "warmup_iterations": 100000,
        "baseline": "same source with only three disabled trace guards removed",
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "limit_percent": 5,
        "passed": all(sample["overhead_percent"] <= 5 for sample in samples),
    }


def bootstrap(port: int) -> tuple[str, str, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)

    try:
        connection.request("GET", "/", headers={"Connection": "close"})
        response = connection.getresponse()
        assert response.status == 200
        cookie = (response.getheader("Set-Cookie") or "").split(";", 1)[0]
        body = response.read().decode()
        recovery = re.search(r'data-pysx-recovery="([^"]+)"', body)
        assert recovery is not None
        assert cookie

        return cookie, recovery[1], body
    finally:
        connection.close()


async def receive(connection: ClientConnection) -> dict[str, object]:
    value: object = json.loads(await connection.recv())
    assert isinstance(value, dict)

    return cast("dict[str, object]", value)


async def attach(
    port: int, cookie: str, recovery: str
) -> tuple[ClientConnection, dict[str, object]]:
    connection = await connect(
        f"ws://127.0.0.1:{port}/ws?protocol=3&recover=1",
        proxy=None,
        origin=Origin(f"http://127.0.0.1:{port}"),
        additional_headers={"Cookie": cookie},
    )
    await connection.send(
        json.dumps({"t": "hello", "version": 3, "recovery": recovery, "edits": []})
    )

    async with asyncio.timeout(5):
        while True:
            message = await receive(connection)

            if message.get("t") in ("init", "adopted"):
                return connection, message


async def increment(
    connection: ClientConnection, initial: dict[str, object], html: str, clicks: int
) -> None:
    handler = re.search(r'data-pysx-click="([^"]+)"', html)
    assert handler is not None
    revision = initial["state_rev"]

    for sequence in range(1, clicks + 1):
        await connection.send(
            json.dumps(
                {
                    "t": "event",
                    "h": handler[1],
                    "version": 3,
                    "epoch": initial["epoch"],
                    "state_rev": revision,
                    "seq": sequence,
                }
            )
        )

        async with asyncio.timeout(5):
            while True:
                message = await receive(connection)
                revision = message["state_rev"]

                if message.get("t") == "ack":
                    assert message.get("status") in ("success", "duplicate")

                    break


def count_html(html: str) -> int:
    match = re.search(r'id="measure-count"[^>]*>(.*?)</p>', html, re.S)
    assert match is not None

    return int(re.sub(r"<[^>]*>", "", match[1]))


async def development_measurements(root: Path, report: dict[str, object]) -> None:
    supervisor = Supervisor(WatchConfig("user_app:app", (root,)))
    clients: list[ClientConnection] = []
    samples: list[ReloadSample] = []
    imports: list[float] = []
    report["reload_samples"] = samples

    try:
        started = time.perf_counter()
        await supervisor.replace()
        report["cold_ready_seconds"] = time.perf_counter() - started
        assert supervisor.error is None, supervisor.error
        report["cold_app_import_seconds"] = supervisor.import_seconds

        async with serve(
            supervisor.websocket, "127.0.0.1", 0, process_request=supervisor.http, close_timeout=1
        ) as server:
            port = server.sockets[0].getsockname()[1]
            credentials: list[tuple[str, str]] = []

            for index in range(2):
                cookie, recovery, body = await asyncio.to_thread(bootstrap, port)
                client, initial = await attach(port, cookie, recovery)
                clients.append(client)
                credentials.append((cookie, recovery))
                await increment(client, initial, str(initial.get("html", body)), index + 1)

            for run in range(1, 6):
                path = root / "leaf_000.py"
                path.write_text(path.read_text().replace(f"build-{run - 1}", f"build-{run}"))
                started = time.perf_counter()
                sample: ReloadSample = {"seconds": 0, "states": [8, 9], "workers": 0, "error": None}
                samples.append(sample)
                fresh: list[ClientConnection] = []

                try:
                    await supervisor.replace()
                    assert supervisor.error is None, supervisor.error
                    assert supervisor.fresh_generation == 1
                    imports.append(supervisor.import_seconds)

                    for index, (cookie, recovery) in enumerate(credentials):
                        client, initial = await attach(port, cookie, recovery)
                        fresh.append(client)
                        assert initial.get("recovered") is True
                        html = str(initial["html"])
                        assert f"build-{run}" in html
                        assert count_html(html) == 8 + index
                except Exception as error:
                    sample["error"] = f"{type(error).__name__}: {error}"

                    raise
                finally:
                    sample["seconds"] = time.perf_counter() - started
                    sample["workers"] = len(supervisor.workers)

                for client in clients:
                    await client.close()
                clients = fresh
            report["warm_app_import_seconds"] = imports
            report["reload_p95_seconds"] = sorted(float(sample["seconds"]) for sample in samples)[
                math.ceil(0.95 * len(samples)) - 1
            ]
            report["reload_limit_seconds"] = 1
            report["reload_passed"] = all(
                sample["seconds"] <= 1 and sample["workers"] == 1 and sample["error"] is None
                for sample in samples
            )

            for client in clients:
                await client.close()
            clients.clear()
            server.close()
            server.server.abort_clients()

        analysis = Supervisor(WatchConfig("user_app:app", (root,), inspect=True))
        durations: list[float] = []

        for _ in range(2):
            analysis.request_analysis([str(path) for path in root.glob("*.py")], changed=True)
            assert analysis.analysis_task is not None
            await analysis.analysis_task
            assert analysis.analysis_error is None, analysis.analysis_error
            durations.append(analysis.analysis_seconds)
        files = cast("list[dict[str, object]]", analysis.analysis_report["files"])
        report["analysis_seconds"] = durations
        report["analysed_files"] = len(files)
        report["diagnostic_count"] = sum(
            len(cast("list[object]", file["diagnostics"])) for file in files
        )
        assert len(files) == 200
    finally:
        for client in clients:
            await client.close()

        for worker in supervisor.workers:
            await worker.close()


async def visible_measurements(root: Path, kind: str) -> dict[str, object]:
    target = "examples.counter:app" if kind == "counter" else "user_app:app"
    path = root / "examples" / "counter.py" if kind == "counter" else root / "leaf_000.py"
    config = WatchConfig(target, (root,))
    supervisor = Supervisor(config)
    watching: asyncio.Task[None] | None = None

    try:
        await supervisor.replace()
        assert supervisor.error is None, supervisor.error
        watching = asyncio.create_task(supervisor.watch(config.snapshot()))

        async with serve(
            supervisor.websocket, "127.0.0.1", 0, process_request=supervisor.http, close_timeout=1
        ) as server:
            port = server.sockets[0].getsockname()[1]
            process = await asyncio.create_subprocess_exec(
                "node",
                str(PROJECT / "tests" / "dev_visible_measurements.mjs"),
                str(port),
                str(path),
                kind,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=PROJECT / "tests",
            )

            try:
                async with asyncio.timeout(180):
                    stdout, stderr = await process.communicate()
                assert process.returncode == 0, stderr.decode()
                result = cast("dict[str, object]", json.loads(stdout))
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            server.close()
            server.server.abort_clients()
        samples = cast("list[dict[str, object]]", result["samples"])
        durations = [float(cast("float", sample["visible_seconds"])) for sample in samples]
        result["median_seconds"] = statistics.median(durations)
        result["default_interval_seconds"] = config.interval
        result["default_debounce_seconds"] = config.debounce
        result["passed"] = len(samples) == 5 and all(sample["workers"] == 1 for sample in samples)

        if kind == "counter":
            result["baseline_median_seconds"] = 0.625
            result["passed"] = bool(result["passed"] and statistics.median(durations) < 0.625)

        return result
    finally:
        supervisor.stopping.set()

        if watching is not None:
            watching.cancel()
            await asyncio.gather(watching, return_exceptions=True)

        for worker in supervisor.workers:
            await worker.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--section", choices=("reload", "all"), default="all")
    args = parser.parse_args()
    target = args.report.resolve()
    report: dict[str, object] = {
        "profile": "development_focus",
        "reference_qualified": False,
        "qualification_pending": "reference runner, sustained load and browser paint",
        "environment": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
            "pysx": importlib.metadata.version("pysx"),
            "websockets": importlib.metadata.version("websockets"),
        },
    }

    try:
        if args.section == "all":
            report["disabled_overhead"] = disabled_overhead()
            report["tracing_qualification"] = (
                "separate pending qualification; reported, not gated here"
            )
            report["capture_overhead"] = capture_overhead()
        report["preservation_scale"] = preservation_scale()

        with tempfile.TemporaryDirectory(prefix="pysx-measurements-") as directory:
            root = Path(directory)
            create_workspace(root)
            report["workload"] = describe_workspace(root)

            with chdir(root):
                asyncio.run(development_measurements(root, report))
        with tempfile.TemporaryDirectory(prefix="pysx-visible-") as directory:
            base = Path(directory)
            counter = base / "counter"
            shutil.copytree(
                PROJECT / "examples",
                counter / "examples",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            with chdir(counter):
                report["counter_visible"] = asyncio.run(visible_measurements(counter, "counter"))
            large = base / "large"
            create_workspace(large, ordinary=True)
            report["ordinary_workload"] = describe_workspace(large)
            with chdir(large):
                report["large_visible"] = asyncio.run(visible_measurements(large, "large"))
        report["passed"] = bool(
            report["reload_passed"]
            and report["counter_visible"]["passed"]
            and report["large_visible"]["passed"]
            and (
                args.section == "reload"
                or cast("dict[str, object]", report["capture_overhead"])["passed"]
            )
        )
    except Exception as error:
        report["passed"] = False
        report["error"] = f"{type(error).__name__}: {error}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n")
    print("DEV MEASUREMENTS PASSED" if report["passed"] else "DEV MEASUREMENTS FAILED")  # noqa: T201

    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
