"""Operational probes and the executable Linux reference topology."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import TYPE_CHECKING

import pytest
from deployment_topology import Deployment, linux_containers

from pysx import DurableApplication, HostConfig, SQLiteOwnershipStore, StateCodec, native
from pysx.request import RequestContext
from pysx.security import SecurityPolicy

if TYPE_CHECKING:
    from pathlib import Path


def test_operational_topology_probe_store_failure_drain_and_redaction(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    store = SQLiteOwnershipStore(path)

    def decode(value: object) -> int:
        if type(value) is not int:
            raise ValueError("expected integer")

        return value

    # Use a concrete factory so both strict checkers retain the root state type.
    host = DurableApplication(
        lambda state: native.Span(state),
        initial=lambda: 0,
        codec=StateCodec[int](lambda value: value, decode),
        store=store,
        application="private-application",
        worker="private-worker",
    )

    async def exercise() -> None:
        request = RequestContext(path="/_pysx/ready")
        assert (await host.http(request)).status == 200
        host.runtime.reservations = host.runtime.limits.graphs
        assert (await host.http(request)).status == 503
        host.runtime.reservations = 0
        assert (await host.http(request)).status == 200
        moved = tmp_path / "lost.db"
        path.rename(moved)
        assert (await host.http(request)).status == 503
        moved.rename(path)
        assert (await host.http(request)).status == 200
        host.begin_drain()
        assert (await host.http(request)).status == 503
        assert (await host.http(RequestContext(path="/health"))).status == 200
        metrics = await host.http(RequestContext(path="/_pysx/metrics"))
        assert metrics.status == 200
        assert b"pysx_durable_draining 1" in metrics.body
        assert b"pysx_durable_store_failures_total 1" in metrics.body
        assert b"private" not in metrics.body
        head = await host.http(RequestContext(path="/_pysx/metrics"), method="HEAD")
        assert head.body == b""
        await host.close()
        assert (await host.http(request)).status == 503
        assert host.renewal is not None
        assert host.renewal.done()

    asyncio.run(exercise())


@pytest.mark.skipif(
    not linux_containers(),
    reason="the reference topology needs a Linux container runtime, which this host has not got",
)
def test_operational_topology_linux_proxy_adapters_drain_and_cleanup(tmp_path: Path) -> None:
    with Deployment(tmp_path) as topology:
        for worker in ("worker-a", "worker-b"):
            assert topology.probe(worker)[0] == 200
            status, html = topology.public("/deep/link?owner=" + worker)
            assert status == 200
            assert 'data-pysx-owner="' + worker + '"' in html
            assert "Linux durable deployment" in html
        assert topology.public("/_pysx/metrics")[0] == 404
        assert topology.public("/_fixture/drain")[0] == 404
        topology.inside(
            "worker-a",
            "from pathlib import Path; Path('/state/state.sqlite3').rename('/state/lost.sqlite3')",
        )

        try:
            assert topology.probe("worker-a")[0] == 503
            assert topology.probe("worker-b")[0] == 503
        finally:
            topology.inside(
                "worker-a",
                "from pathlib import Path; "
                "Path('/state/lost.sqlite3').rename('/state/state.sqlite3')",
            )
        assert topology.probe("worker-a")[0] == 200
        assert topology.probe("worker-b")[0] == 200
        topology.inside(
            "worker-a",
            "import sqlite3; s=sqlite3.connect('/state/state.sqlite3'); "
            "d=sqlite3.connect('/state/backup.sqlite3'); s.backup(d); d.close(); s.close()",
        )
        backup = tmp_path / "backup.sqlite3"
        topology.command("cp", "worker-a:/state/backup.sqlite3", str(backup))
        with sqlite3.connect(backup) as database:
            assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert database.execute("SELECT count(*) FROM state").fetchone()[0] >= 2
        topology.command("kill", "-s", "SIGUSR1", "worker-a")
        assert topology.probe("worker-a")[0] == 503
        assert topology.probe("worker-a", "/health")[0] == 200
        assert topology.public("/new?owner=worker-a")[0] == 200
        topology.command("restart", "worker-a")
        topology.wait_ready()
        assert topology.probe("worker-a")[0] == 200


def test_operational_topology_metrics_obey_the_host_access_policy(tmp_path: Path) -> None:
    def decode(value: object) -> int:
        if type(value) is not int:
            raise ValueError("expected integer")

        return value

    def authorize(_request: RequestContext, action: str, _target: str) -> bool:
        return action != "http"

    host = DurableApplication(
        lambda state: native.Span(state),
        initial=lambda: 0,
        codec=StateCodec[int](lambda value: value, decode),
        store=SQLiteOwnershipStore(tmp_path / "state.db"),
        application="private-application",
        worker="private-worker",
        config=HostConfig(security=SecurityPolicy(authorize=authorize)),
    )

    async def exercise() -> None:
        try:
            denied = await host.http(RequestContext(path="/_pysx/metrics"))
            assert denied.status == 403
            assert b"pysx_durable_" not in denied.body
            assert any(name == "x-request-id" for name, _ in denied.headers)
            assert host.metrics.get("http:denied") == 1
            ready = await host.http(RequestContext(path="/_pysx/ready"))
            assert ready.status == 200
        finally:
            await host.close()

    asyncio.run(exercise())
