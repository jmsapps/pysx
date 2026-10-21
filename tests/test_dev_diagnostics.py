"""Inspector ownership, value redaction, shared ranges and bounded worker cleanup."""

from __future__ import annotations

import ast
import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from dev_measurements import WithoutCaptureHook, WithoutTraceGuards, count_html
from dev_workload import (
    COMPONENTS,
    FILES,
    LARGEST_BYTES,
    TOTAL_BYTES,
    create_workspace,
    describe_workspace,
)

from pysx import Application, HostConfig, SecurityPolicy, native, signal
from pysx.development import tracing
from pysx.development.control import Control, control_server
from pysx.development.development import Supervisor, WatchConfig
from pysx.development.diagnostics import analyse
from pysx.hosting.hosting import request_from_headers
from pysx.runtime.session import Session
from pysx.tooling.check import diagnostics_for_source

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pysx import Fragment, Signal
    from pysx.runtime.wire import JSONValue


def test_inspector_tracing_excludes_other_nested_sdk_frames() -> None:
    root = Path(__file__).resolve().parents[1]
    tracing.configure(enable=True, source_roots=(root,))
    namespace: dict[str, object] = {"signal": signal}
    exec(
        compile(
            "def nested():\n    return signal(0)\n",
            str(root / "pysx/runtime/nested_frame.py"),
            "exec",
        ),
        namespace,
    )
    trace = tracing.Trace()
    token = tracing.active.set(trace)

    try:
        value = cast("Callable[[], Signal[int]]", namespace["nested"])()
        value.set(1)
        assert trace.events
        assert all(event["source"] is not None for event in trace.events)
        assert all(
            event["source"]["file"] == "tests/test_dev_diagnostics.py"
            for event in trace.events
            if event["source"] is not None
        )
    finally:
        tracing.active.reset(token)
        tracing.configure(enable=False)


def test_inspector_tracing_local_reports_redact_values_and_count_actual_restores() -> None:
    from pysx.components.lifecycle import capture_local_state

    def view() -> Fragment:
        keep = signal(0)
        signal(0)

        return native.Main(keep)

    async def run() -> None:
        tracing.configure(enable=False, source_roots=(Path(__file__).parent,))
        capture_local_state(enable=True)
        original = Application(view, config=HostConfig(development=True))
        candidate = Application(view, config=HostConfig(development=True))
        request = request_from_headers("/", [("Cookie", "pysx_browser=" + "b" * 32)])

        try:
            graph = original.runtime.create_reserved(request)
            token = original.runtime.attach(graph, request)
            slots = list(graph.rendered.scopes.owners[""].slots.values())
            slots[0].publish(7)
            slots[1].publish("sensitive-state-panel-value")
            frozen = cast("dict[str, JSONValue]", Control(original).freeze_local())
            rows = cast("list[JSONValue]", json.loads(json.dumps(frozen["rows"])))
            control = Control(candidate)
            await control.validate_local(rows)
            control.seed_local(rows)
            candidate.runtime.resume(token, request, graph.transport.epoch, 0, location="/")
            report = cast("dict[str, JSONValue]", control.local_report())
            assert report["preserved"] == 1
            assert report["scope"] == "workspace"
            resets = cast("list[dict[str, JSONValue]]", report["reset"])
            assert len(resets) == 1
            assert resets[0]["reason"] == "value type changed"
            source = cast("dict[str, JSONValue]", resets[0]["source"])
            assert str(source["uri"]).startswith("vscode://file/")
            assert str(source["file"]) == Path(__file__).name
            assert "sensitive-state-panel-value" not in json.dumps(report)
            fresh = candidate.runtime.create_reserved(request)
            assert not fresh.rendered.scopes.owners[""].preserved
            assert cast("dict[str, JSONValue]", control.local_report())["preserved"] == 1
        finally:
            await original.close()
            await candidate.close()
            capture_local_state(enable=False)
            tracing.configure(enable=False)

    asyncio.run(run())


def test_inspector_tracing_matches_graph_metadata_and_omits_values() -> None:
    tracing.configure(enable=True, source_roots=(Path(__file__).parent,))
    values: list[Signal[int]] = []

    def view() -> Fragment:
        value = signal(0)
        private = signal({"password": "never-collect-this-secret"})
        values.append(value)
        # Reading a secret in an effect must capture only its dependency identity.
        from pysx import own_effect

        own_effect("private", lambda: private())

        return native.P(value)

    session = Session(view, development=True)

    try:
        assert session.trace is not None

        with session.context():
            for value in range(1, 400):
                values[0].set(value)
        report = session.trace.report()
        serialized = json.dumps(report)
        assert "never-collect-this-secret" not in serialized
        assert "password" not in serialized
        assert "[redacted]" in serialized
        assert len(session.trace.events) == 256
        assert session.trace.dropped > 0
        assert session.trace.counts["signal.write"] == 399
        assert session.trace.counts["dependency"] >= 399
        assert session.trace.counts["effect"] >= 399
        assert session.trace.counts["template"] == 1
        assert session.trace.counts["patch.queue"] == 399
        assert any(
            event["source"] and event["source"]["line"] > 0 for event in session.trace.events
        )
    finally:
        session.dispose()
        tracing.configure(enable=False)


def test_inspector_tracing_disabled_has_no_buffers_and_production_stays_disabled() -> None:
    tracing.configure(enable=False)
    session = Session(lambda: native.P("ordinary"), development=True)
    assert session.trace is None
    session.dispose()
    tracing.configure(enable=True, source_roots=(Path(__file__).parent,))

    try:
        outer = tracing.Trace()
        token = tracing.active.set(outer)

        try:
            production = Session(lambda: native.P(signal("production")))
            assert production.trace is None
            assert not outer.events
            production.dispose()
        finally:
            tracing.active.reset(token)
        app = Application(lambda: native.P("ordinary"))
        assert Control(app).inspect({}) == {"status": 404}

        async def denied() -> None:
            with pytest.raises(ValueError, match="explicit development"):
                async with control_server(app, 0, "t" * 40):
                    pytest.fail("control must not start")
            await app.close()

        asyncio.run(denied())
    finally:
        tracing.configure(enable=False)


def test_inspector_tracing_source_lookup_failure_cannot_break_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_args: object) -> tracing.Source:
        raise OSError("source unavailable")

    monkeypatch.setattr(tracing, "source_link", unavailable)
    tracing.configure(enable=True, source_roots=(Path(__file__).parent,))
    values: list[Signal[int]] = []

    def view() -> Fragment:
        value = signal(0)
        values.append(value)

        return native.P(value)

    session = Session(view, development=True)

    try:
        with session.context():
            values[0].set(1)
        assert values[0]() == 1
        assert session.trace is not None
        assert session.trace.counts["patch.queue"] == 1
        assert all(event["source"] is None for event in session.trace.events)
    finally:
        session.dispose()
        tracing.configure(enable=False)


def test_inspector_tracing_requires_browser_principal_and_authorization() -> None:
    tracing.configure(enable=True, source_roots=(Path(__file__).parent,))
    app = Application(
        lambda: native.P(signal("private-state")),
        config=HostConfig(
            development=True,
            principal=lambda request: request.headers.get("x-user"),
            security=SecurityPolicy(
                require_principal=True,
                authorize=lambda request, action, _target: (
                    action != "inspector" or request.principal == "alice"
                ),
            ),
        ),
    )
    alice = {"Host": "localhost", "Cookie": "pysx_browser=" + "a" * 32, "x-user": "alice"}
    bob = {"Host": "localhost", "Cookie": "pysx_browser=" + "b" * 32, "x-user": "bob"}

    try:
        graphs: list[Session] = []

        for headers in (alice, bob):
            request = app.prepare(request_from_headers("/", list(headers.items())))
            graph = app.runtime.create_reserved(request)
            app.runtime.attach(graph, request)
            graphs.append(graph)
        report = Control(app).inspect(alice)
        assert report["status"] == 200
        assert graphs[0].trace is not None
        assert graphs[1].trace is not None
        assert graphs[0].trace.uid in json.dumps(report)
        assert graphs[1].trace.uid not in json.dumps(report)
        assert "private-state" not in json.dumps(report)
        assert Control(app).inspect(bob)["status"] == 403
        assert Control(app).inspect({**alice, "Cookie": bob["Cookie"]})["status"] == 403
        assert Control(app).inspect({**bob, "Cookie": alice["Cookie"]})["status"] == 403
        assert Control(app).inspect({})["status"] == 403
    finally:
        asyncio.run(app.close())
        tracing.configure(enable=False)


def test_inspector_tracing_static_codes_and_utf16_crlf_ranges_are_shared(tmp_path: Path) -> None:
    source = (
        "from pysx import pysx\n"
        'def app():\n    emoji = "🐍"; return pysx(t\'div(noSuchAttribute="x")\')\n'
    ).replace("\n", "\r\n")
    path = tmp_path / "source.py"
    path.write_bytes(source.encode())
    expected = diagnostics_for_source(source, str(path), workspace_roots=(tmp_path,))
    assert expected
    report = analyse([str(path)], (tmp_path,))
    files = cast("list[dict[str, JSONValue]]", report["files"])
    assert files[0]["diagnostics"] == expected
    marker = tmp_path / "executed"
    path.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    analyse([str(path)], (tmp_path,))
    assert not marker.exists()


def test_inspector_tracing_analysis_is_coalesced_and_reaps_children(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        path = tmp_path / "source.py"
        path.write_text(
            "from pysx import pysx\ndef app():\n    return pysx(t'div(firstBad=\"x\")')\n"
        )
        original = asyncio.create_subprocess_exec
        spawned: list[asyncio.subprocess.Process] = []

        async def spawn(
            *args: str,
            stdin: int | None = None,
            stdout: int | None = None,
            stderr: int | None = None,
            start_new_session: bool = False,
            env: Mapping[str, str] | None = None,
        ) -> asyncio.subprocess.Process:
            process = await original(
                *args,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                start_new_session=start_new_session,
                env=env,
            )
            spawned.append(process)

            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        supervisor = Supervisor(WatchConfig("source:app", (tmp_path,), inspect=True))
        supervisor.request_analysis([str(path)])

        async with asyncio.timeout(5):
            while supervisor.analysis_worker is None:
                await asyncio.sleep(0.001)
        task = supervisor.analysis_task
        source = "from pysx import pysx\ndef app():\n    return pysx(t'div(latestBad=\"x\")')\n"
        path.write_text(source)

        for _ in range(3):
            supervisor.request_analysis([str(path)], changed=True)
            assert supervisor.analysis_task is task
        assert task is not None
        await task
        assert supervisor.analysis_error is None
        records = cast("list[dict[str, JSONValue]]", supervisor.analysis_report["files"])
        assert records[0]["diagnostics"] == diagnostics_for_source(
            source, str(path), workspace_roots=(tmp_path,)
        )
        assert len(spawned) == 2
        assert supervisor.analysis_worker is None
        assert all(process.returncode is not None for process in spawned)

        supervisor.request_analysis([str(path)], changed=True)

        async with asyncio.timeout(5):
            while supervisor.analysis_worker is None:
                await asyncio.sleep(0.001)
        worker = supervisor.analysis_worker
        assert supervisor.analysis_task is not None
        supervisor.analysis_task.cancel()
        await asyncio.gather(supervisor.analysis_task, return_exceptions=True)
        assert worker.process.returncode is not None

        with pytest.raises(ProcessLookupError):
            os.kill(worker.process.pid, 0)

    asyncio.run(run())


def test_inspector_tracing_fixed_workspace_matches_profile(tmp_path: Path) -> None:
    create_workspace(tmp_path)
    report = describe_workspace(tmp_path)
    assert report["files"] == FILES
    assert report["components"] == COMPONENTS
    assert report["bytes"] == TOTAL_BYTES
    assert report["largest_bytes"] == LARGEST_BYTES
    assert report["active_imports"] == 20

    with pytest.raises(ValueError, match="empty"):
        create_workspace(tmp_path)


def test_inspector_tracing_ordinary_large_workspace_activates_every_component(
    tmp_path: Path,
) -> None:
    create_workspace(tmp_path, ordinary=True)
    report = describe_workspace(tmp_path)
    assert report["components"] == 500
    assert report["files"] == 200
    assert report["bytes"] == TOTAL_BYTES
    assert report["active_imports"] == 199
    source = (tmp_path / "user_app.py").read_text()
    assert "DurableApplication" not in source
    assert "for module in MODULES" in source


@pytest.mark.parametrize(
    "content", ["8", '<pysx-slot id="f2:1">8</pysx-slot>', "<!--slot-->8<!--end-->"]
)
def test_inspector_tracing_measurement_parses_renderer_slots(content: str) -> None:
    assert count_html(f'<p id="measure-count">{content}</p>') == 8


def test_inspector_tracing_measurement_baseline_cannot_strip_behavior() -> None:
    tree = ast.parse('if trace_enabled:\n    _tracing.emit("effect")\n    state.set(3)\n')

    with pytest.raises(ValueError, match="non-tracing behavior"):
        WithoutTraceGuards().visit(tree)


@pytest.mark.parametrize(
    "source",
    [
        'if created is not None:\n    created(cast("Signal[object]", out))\n    state.set(3)\n',
        (
            'if created is not None:\n    created(cast("Signal[object]", out))\n'
            "else:\n    state.set(3)\n"
        ),
        "if created is not None:\n    state.set(3)\n",
    ],
)
def test_capture_measurement_baseline_cannot_strip_behavior(source: str) -> None:
    with pytest.raises(ValueError, match="non-capture behavior"):
        WithoutCaptureHook().visit(ast.parse(source))
