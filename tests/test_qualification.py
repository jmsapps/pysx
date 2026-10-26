"""Qualification checks reject incomplete work and measure actual semantic work."""

import asyncio
import json
from typing import TYPE_CHECKING, cast

import pytest
import qualification
from qualification import graph_workload, keyed_workload, main, workloads

if TYPE_CHECKING:
    from pathlib import Path

    from websockets.asyncio.client import ClientConnection

    from pysx.runtime.wire import JSONValue


def test_fixed_workloads_graph_counts() -> None:
    assert graph_workload()["notifications"] == 1


def test_fixed_workloads_keyed_counts() -> None:
    assert keyed_workload()["edit_ops"] == 1


def test_fixed_workloads_workspace() -> None:
    assert workloads()["editor_workspace"]


def test_fixed_workloads_exception_replaces_stale_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> dict[str, JSONValue]:
        raise ValueError("incomplete workload")

    monkeypatch.setattr(qualification, "workloads", broken)
    report = tmp_path / "report.json"
    report.write_text('{"status": "passed"}')
    assert main(["--suite", "workloads", "--report", str(report)]) == 1
    assert json.loads(report.read_text())["status"] == "failed"


@pytest.mark.parametrize("args", [[], ["--suite", "unknown"], ["--suite", "workloads"]])
def test_fixed_workloads_required_inputs(args: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_fixed_workloads_report(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    assert main(["--suite", "workloads", "--report", str(report)]) == 0
    result = json.loads(report.read_text())
    assert result["status"] == "passed"
    assert set(result["metrics"]) == {"graph", "keyed", "editor_workspace", "sessions", "inspector"}
    assert result["environment"]["runner_sha256"]


def test_fixed_workloads_missing_metrics_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(qualification, "workloads", dict)
    report = tmp_path / "report.json"
    assert main(["--suite", "workloads", "--report", str(report)]) == 1
    result = json.loads(report.read_text())
    assert result["status"] == "failed"
    assert "missing required graph metrics" in result["error"]


@pytest.mark.parametrize(
    "output",
    ["", 'QUALIFICATION BROWSER SAMPLES: {"engine":"chromium","rows":1000,"samples_seconds":[1]}'],
)
def test_editor_browser_missing_engines_or_samples_fail(output: str) -> None:
    from qualification_tooling import browser_samples

    with pytest.raises(ValueError, match="missing"):
        browser_samples(output)


@pytest.mark.parametrize("suite", ["runtime", "tooling"])
def test_editor_browser_missing_measurement_groups_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suite: str
) -> None:
    import qualification_runtime
    import qualification_tooling

    def missing(_directory: Path) -> dict[str, JSONValue]:
        return {}

    monkeypatch.setattr(qualification_runtime, "runtime", missing)
    monkeypatch.setattr(qualification_tooling, "tooling", missing)
    target = tmp_path / "missing.json"
    assert main(["--suite", suite, "--report", str(target)]) == 1
    assert "missing required measurement groups" in json.loads(target.read_text())["error"]


def test_editor_browser_all_stops_at_failed_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []

    def measure(suite: str, _directory: Path) -> dict[str, JSONValue]:
        called.append(suite)

        if suite == "runtime":
            raise RuntimeError("fault failed")

        return {"observed": True}

    monkeypatch.setattr(qualification, "measure", measure)
    target = tmp_path / "all.json"
    assert main(["--suite", "all", "--report", str(target)]) == 1
    assert called == ["workloads", "runtime"]
    assert json.loads(target.read_text())["status"] == "failed"
    assert json.loads((tmp_path / "workloads.json").read_text())["status"] == "passed"


def test_load_churn_disposed_graphs_and_rows() -> None:
    from qualification_runtime import churn

    result = churn()
    assert result["disposed_graphs"] == 1000
    assert result["mounted_removed_rows"] == 10000
    assert result["retained_graphs"] == 0


@pytest.mark.parametrize("samples", [[], [-1.0], [float("nan")], [float("inf")]])
def test_load_churn_invalid_samples(samples: list[float]) -> None:
    from qualification_runtime import summarize

    with pytest.raises(ValueError, match="samples"):
        summarize(samples)


@pytest.mark.parametrize("status", ["success", "stale"])
def test_load_churn_protocol_client_tracks_bindings_and_rejects_failure(status: str) -> None:
    from ownership_topology import Client
    from qualification_runtime import event_roundtrip

    class Socket:
        def __init__(self) -> None:
            self.frames: list[dict[str, JSONValue]] = [
                {
                    "t": "patch",
                    "state_rev": 1,
                    "ops": [{"op": "attr", "id": "field", "name": "data-pysx-binding", "v": "new"}],
                },
                {
                    "t": "ack" if status == "success" else "result",
                    "seq": 1,
                    "state_rev": 2,
                    "status": status,
                },
            ]

        async def send(self, payload: str) -> None:
            assert json.loads(payload)["h"] == "old"

        async def recv(self) -> str:
            return json.dumps(self.frames.pop(0))

    client = Client(
        cast("ClientConnection", Socket()),
        "browser",
        {
            "t": "init",
            "epoch": "epoch",
            "state_rev": 0,
            "resume": "resume",
            "recovery": "recovery",
            "owner": "owner",
            "html": '<button data-pysx-click="a"></button><button data-pysx-click="b"></button>'
            '<input data-pysx-el="field" data-pysx-binding="old">',
        },
    )

    if status == "stale":
        with pytest.raises(RuntimeError, match="stale"):
            asyncio.run(event_roundtrip(client, "old", "value"))
    else:
        asyncio.run(event_roundtrip(client, "old", "value"))
        assert client.bindings == ["new"]
        assert client.revision == 2


def test_load_churn_event_turns_preserve_idle_leases(tmp_path: Path) -> None:
    from test_connection_pump import FakeConnection
    from test_worker_ownership import durable_host

    from pysx import DurableSession, SQLiteOwnershipStore

    async def exercise() -> None:
        now = [100.0]
        backend = SQLiteOwnershipStore(tmp_path / "event-leases.db", clock=lambda: now[0])
        host = durable_host(backend, lease=0.6)
        await host.start()
        request = FakeConnection("/ws?protocol=3", []).request
        idle = cast("DurableSession[int]", host.runtime.create_reserved(request))
        active = cast("DurableSession[int]", host.runtime.create_reserved(request))

        try:
            for _ in range(10):
                now[0] += 0.15
                host.renewed -= 0.3
                # Lock acquisition is immediate: the periodic task gets no loop turn.
                async with active.runtime.turn():
                    active.state.set(active.state() + 1)
            host.state_store.check(idle.snapshot)
            assert active.state() == 10
        finally:
            await host.close()

    asyncio.run(exercise())


def test_load_churn_expired_mutation_owner_stays_fenced(tmp_path: Path) -> None:
    from test_connection_pump import FakeConnection
    from test_worker_ownership import durable_host

    from pysx import DurableSession, RecoveryUnavailable, SQLiteOwnershipStore

    async def exercise() -> None:
        now = [100.0]
        backend = SQLiteOwnershipStore(tmp_path / "expired-turn.db", clock=lambda: now[0])
        host = durable_host(backend, lease=0.6)
        await host.start()
        request = FakeConnection("/ws?protocol=3", []).request
        graph = cast("DurableSession[int]", host.runtime.create_reserved(request))

        try:
            now[0] += 0.75
            host.renewed -= 0.75
            with pytest.raises(RecoveryUnavailable, match="ownership unavailable"):
                async with graph.runtime.turn():
                    graph.state.set(99)
            assert graph.closed
            assert graph.state() == 0
            assert backend.route(graph.snapshot.identity) is None
        finally:
            await host.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("admission_renewal", [True, False])
def test_load_churn_admission_prevents_lease_starvation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, admission_renewal: bool
) -> None:
    from test_connection_pump import FakeConnection
    from test_worker_ownership import durable_host

    from pysx import DurableSession, RecoveryUnavailable, SQLiteOwnershipStore

    async def exercise() -> None:
        now = [100.0]
        backend = SQLiteOwnershipStore(tmp_path / "leases.db", clock=lambda: now[0])
        host = durable_host(backend, lease=0.6)
        await host.start()
        request = FakeConnection("/ws?protocol=3", []).request
        first = cast("DurableSession[int]", host.runtime.create_reserved(request))

        if not admission_renewal:
            monkeypatch.setattr(host, "renew_if_overdue", lambda: None)

        try:
            # No await: periodic renewal cannot run during this admission storm.
            for _ in range(10):
                now[0] += 0.15
                host.renewed -= 0.3
                host.runtime.create_reserved(request)

            if admission_renewal:
                host.state_store.check(first.snapshot)
            else:
                with pytest.raises(RecoveryUnavailable, match="fenced"):
                    host.state_store.check(first.snapshot)
        finally:
            await host.close()

    asyncio.run(exercise())
