"""Practical local recovery observations through ordinary host and socket paths."""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import http.client
import json
import os
import platform
import re
import secrets
import subprocess
import sys
import tempfile
import time
import tracemalloc
import weakref
from dataclasses import fields
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, cast

import browser_capture_fixture as fixture
from browser_capture_measurements import summarize
from ownership_topology import Worker, free_port
from websockets.asyncio.client import connect
from websockets.typing import Origin

from pysx import Application, HostConfig
from pysx.components.checkpoints import MAX_NODES, MAX_SLOTS, Captured, Checkpoints, Slot, sites
from pysx.hosting.browser_recovery import BrowserCheckpoint
from pysx.hosting.hosting import request_from_headers
from pysx.hosting.recovery_keys import encode_ring
from pysx.runtime.capsule import MAX_CAPSULE, MAX_PLAINTEXT, CapsuleError, KeyRing, RootKey

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

    from pysx.runtime.session import Session

ROOT = Path(__file__).resolve().parent.parent


def private_keys(directory: Path) -> None:
    directory.mkdir(mode=0o700)
    descriptor = os.open(directory / "recovery.keys", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encode_ring(KeyRing(RootKey(secrets.token_bytes(8), secrets.token_bytes(32)))))


def recovery_bytes(session: Session) -> int:
    """Count recovery-owned metadata/payload; Signal values belong to the app graph."""
    seen: set[int] = set()

    def size(value: object) -> int:
        if id(value) in seen:
            return 0
        seen.add(id(value))
        total = sys.getsizeof(value)

        if isinstance(value, dict):
            total += sum(
                size(key) + size(item) for key, item in cast("dict[object, object]", value).items()
            )
        elif isinstance(value, (list, tuple, set)):
            total += sum(
                size(item)
                for item in cast("list[object] | tuple[object, ...] | set[object]", value)
            )
        elif isinstance(value, Captured):
            total += size(value.__dict__)
        elif isinstance(value, (Slot, BrowserCheckpoint)):
            total += sum(
                size(getattr(value, field.name))
                for field in fields(value)
                if field.name != "signal"
            )

            if isinstance(value, Slot):
                total += size(value.signal.checkpoint_changed)
        elif isinstance(value, Checkpoints):
            total += size(value.__dict__)

        return total

    return size(session.rendered.scopes.checkpoints) + size(session.browser_checkpoint)


def retained_bytes() -> int:
    snapshot = tracemalloc.take_snapshot().filter_traces(
        [
            tracemalloc.Filter(True, str(ROOT / "pysx" / "*")),
        ]
    )

    return sum(trace.size for trace in snapshot.traces)


def peak_rss_bytes() -> int | None:
    if sys.platform == "win32":
        return None
    import resource

    maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    return int(maximum if sys.platform == "darwin" else maximum * 1024)


async def history(count: int, directory: Path) -> dict[str, object]:
    if count < 1:
        raise ValueError("history needs nonempty sessions")
    key_directory = directory / "keys"
    private_keys(key_directory)
    host = Application(
        fixture.native_app,
        config=HostConfig(
            recovery_namespace="local-history",
            recovery_key_directory=key_directory,
        ),
    )
    await host.start()
    service = host.runtime.browser_recovery
    assert service is not None
    timings: list[float] = []
    samples: list[dict[str, int]] = []
    extra: list[int] = []
    weak: list[weakref.ReferenceType[Session]] = []
    max_graphs = 0

    async def issue(index: int, *, retain: bool = False) -> Session | None:
        nonlocal max_graphs
        request = request_from_headers(
            "/",
            [
                ("host", "127.0.0.1:7000"),
                ("cookie", f"pysx_browser=h{index:031d}"),
            ],
        )
        started = time.perf_counter()
        response = await host.http(request)
        assert response.status == 200
        assert response.adoption is not None
        graph = host.runtime.pending[response.adoption].session
        state = service.checkpoint(graph)
        assert state.token is not None
        assert len(state.token) <= MAX_CAPSULE
        timings.append(time.perf_counter() - started)
        max_graphs = max(max_graphs, len(host.runtime.graphs))
        extra.append(recovery_bytes(graph))

        if retain:
            return host.runtime.claim(response.adoption, request, graph.browser)
        weak.append(weakref.ref(graph))
        host.runtime.expire(response.adoption)

        return None

    started_trace = not tracemalloc.is_tracing()

    try:
        for index in range(20):
            await issue(count + index)
        weak.clear()
        timings.clear()
        extra.clear()
        fixture.held.clear()
        await asyncio.sleep(0)
        gc.collect()

        if started_trace:
            tracemalloc.start()
        baseline = retained_bytes()

        for index in range(count):
            await issue(index)

            if (index + 1) % 64 == 0 or index + 1 == count:
                fixture.held.clear()
                await asyncio.sleep(0)
                gc.collect()
                assert all(reference() is None for reference in weak)
                weak.clear()
                assert not host.runtime.graphs
                assert not host.runtime.pending
                assert not host.runtime.resume_handles
                assert not host.runtime.detached
                samples.append({"sessions": index + 1, "package_retained_bytes": retained_bytes()})
        history_times = list(timings)
        live: list[Session] = []

        for index in range(4):
            graph = await issue(count + 100 + index, retain=True)
            assert graph is not None
            live.append(graph)
        changed: list[float] = []
        unchanged: list[float] = []

        for index in range(200):
            graph = live[index % len(live)]
            owned = next(
                slot.signal
                for slot in graph.rendered.scopes.checkpoints.slots.values()
                if slot.kind == "str"
            )
            started = time.perf_counter()
            async with graph.runtime.turn():
                owned.set(f"turn-{index}")
            graph.pending.clear()
            service.publish(graph, graph.request)
            changed.append(time.perf_counter() - started)
            started = time.perf_counter()
            assert service.publish(graph, graph.request) == {}
            unchanged.append(time.perf_counter() - started)
            await issue(count + 1000 + index)
            assert len(host.runtime.graphs) == 4
            await asyncio.sleep(0)
            weak.clear()
        graph = live[0]
        owned = next(
            slot.signal
            for slot in graph.rendered.scopes.checkpoints.slots.values()
            if slot.kind == "str"
        )
        async with graph.runtime.turn():
            owned.set("u" * 20000)
        graph.pending.clear()
        current, _peak = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        checkpoint = service.publish(graph, graph.request)
        _current, peak = tracemalloc.get_traced_memory()
        scratch = peak - current
        assert scratch <= 2 * 1024 * 1024
        assert isinstance(checkpoint["capsule"], str)
        assert len(checkpoint["capsule"]) <= MAX_CAPSULE
        extra.append(recovery_bytes(graph))

        for malformed in ("!", "a" * (MAX_CAPSULE + 1)):
            try:
                service.issuer.open(
                    malformed,
                    service.context(graph, graph.request, service.checkpoint(graph).document),
                )
            except CapsuleError:
                pass
            else:
                raise AssertionError("malformed capsule accepted")

        for graph in live:
            host.runtime.release(graph)
        live.clear()
        fixture.held.clear()
        gc.collect()
        growth = samples[-1]["package_retained_bytes"] - baseline
        assert growth <= 256 * 1024
        assert max(extra) <= 256 * 1024
        assert sites.cache_info().currsize <= 128
        assert not host.runtime.graphs
        assert not host.runtime.pending
        assert not host.runtime.resume_handles
        assert not host.runtime.detached
        assert service.executing == service.waiting == 0

        return {
            "issued_and_expired": count,
            "warmup_sessions": 20,
            "total_issued_sessions": count + 224,
            "fixture": "two owned slots; near-limit string capture measured separately",
            "batch_size": 64,
            "retained_samples": samples,
            "package_retained_growth_bytes": growth,
            "peak_runner_rss_bytes": peak_rss_bytes(),
            "memory_accounting": {
                "retention": "package-attributed traced bytes after GC; runner samples excluded",
                "rss": "peak runner RSS including the in-process host",
                "extra": "shallow recovery inventory; shared values once, app payloads excluded",
                "scratch": "traced peak above the pre-capture baseline",
            },
            "timing_instrumentation": "tracemalloc enabled during history and turn timings",
            "max_recovery_owned_bytes": max(extra),
            "capture_scratch_peak_bytes": scratch,
            "max_observed_graphs": max_graphs,
            "fixed_live_graphs": 4,
            "churn_turns": 200,
            "transient_churn_sessions": 200,
            "malformed_capsules_rejected": 2,
            "historical_ownership_after_expiry": 0,
            "http_render_and_capsule": summarize(history_times),
            "changed_turn_and_capsule": summarize(changed),
            "unchanged_cached_publication": summarize(unchanged),
            "code_cache_entries": sites.cache_info().currsize,
            "key_files": [path.name for path in key_directory.iterdir()],
            "key_bytes": (key_directory / "recovery.keys").stat().st_size,
        }
    finally:
        await host.close()
        fixture.held.clear()

        if started_trace and tracemalloc.is_tracing():
            tracemalloc.stop()


def get_worker(port: int, authority: str, path: str = "/health") -> tuple[int, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)

    try:
        connection.request(
            "GET", path, headers={"Host": authority, "Cookie": "pysx_browser=" + "b" * 32}
        )
        response = connection.getresponse()

        return response.status, response.read().decode()
    finally:
        connection.close()


class Peer:
    def __init__(self, socket: ClientConnection, document: str) -> None:
        self.socket = socket
        self.document = document
        self.capsule: str | None = None
        self.epoch = ""
        self.revision = 0
        self.sequence = 0
        self.text = ""

    async def read(self, kind: str) -> dict[str, object]:
        async with asyncio.timeout(5):
            while True:
                message = cast("dict[str, object]", json.loads(await self.socket.recv()))
                assert message["version"] == 4
                assert message["base_rev"] == self.revision
                self.revision = cast("int", message["state_rev"])
                self.epoch = cast("str", message["epoch"])

                if "capsule" in message:
                    self.capsule = cast("str | None", message["capsule"])

                if message["t"] == "patch":
                    for operation in cast("list[dict[str, object]]", message["ops"]):
                        if operation["op"] == "text":
                            self.text = cast("str", operation["v"])

                if message["t"] == kind:
                    return message

    async def event(self, handler: str) -> None:
        self.sequence += 1
        await self.socket.send(
            json.dumps(
                {
                    "t": "event",
                    "version": 4,
                    "epoch": self.epoch,
                    "state_rev": self.revision,
                    "seq": self.sequence,
                    "h": handler,
                }
            )
        )
        await self.read("ack")


async def two_workers(directory: Path) -> dict[str, object]:
    directory.mkdir()
    keys = directory / "keys"
    private_keys(keys)
    authority = f"127.0.0.1:{free_port()}"
    public_origin = "http://" + authority
    body = (ROOT / "tests/browser_checkpoint_fixture.py").read_text()
    suffix = (
        "\nfrom pathlib import Path\nfrom pysx import Application, HostConfig, SecurityPolicy\n"
        f"host = Application(app, config=HostConfig(recovery_namespace='local-workers', "
        f"recovery_key_directory=Path({str(keys)!r}), "
        f"security=SecurityPolicy(origins=({public_origin!r},))))\n"
    )
    workers: list[Worker] = []
    sockets: list[ClientConnection] = []

    try:
        for index in range(2):
            root = directory / f"worker-{index}"
            root.mkdir()
            (root / "recovery_worker.py").write_text(
                ("# relocated deployment\n" if index else "") + body + suffix
            )
            port = free_port()
            log = (root / "server.log").open("w+")
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "pysx.hosting.server",
                    "--app",
                    "recovery_worker:host",
                    "--port",
                    str(port),
                ],
                cwd=root,
                text=True,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            worker = Worker(process, port, str(index), log)
            workers.append(worker)

            for _ in range(200):
                if process.poll() is not None:
                    log.seek(0)

                    raise RuntimeError("local worker startup failed: " + log.read())

                try:
                    if get_worker(port, authority)[0] == 200:
                        break
                except OSError:
                    pass
                await asyncio.sleep(0.02)
            else:
                raise RuntimeError("local worker startup deadline")
        status, html = get_worker(workers[0].port, authority, "/")
        assert status == 200

        def attribute(name: str) -> str:
            match = re.search(rf'data-pysx-{name}="([A-Za-z0-9_=\-]+)"', html)
            assert match is not None

            return match[1]

        document, token = attribute("document"), attribute("capsule")
        handler_match = re.search(r'id="increment"[^>]*data-pysx-click="([^"]+)"', html)
        assert handler_match is not None
        handler = handler_match[1]

        async def attach(worker: Worker, query: str, capsule: str, *, adoption: str = "") -> Peer:
            socket = await connect(
                f"ws://{authority}/ws?protocol=4&{query}&location=%2F",
                host="127.0.0.1",
                port=worker.port,
                proxy=None,
                origin=Origin(public_origin),
                additional_headers={"cookie": "pysx_browser=" + "b" * 32},
            )
            sockets.append(socket)
            peer = Peer(socket, document)
            await socket.send(
                json.dumps(
                    {
                        "t": "hello",
                        "version": 4,
                        "document": document,
                        "capsule": capsule,
                        "edits": [],
                    }
                )
            )
            frame = await peer.read("adopted" if adoption else "init")

            if not adoption:
                assert frame["recovered"] is True
                assert re.search(
                    r'id="recovery-count"[^>]*><pysx-slot[^>]*>1</pysx-slot>',
                    cast("str", frame["html"]),
                )

            return peer

        first = await attach(workers[0], "adoption=" + attribute("adoption"), token, adoption="yes")
        await first.event(handler)
        assert first.text == "1"
        assert first.capsule is not None
        token = first.capsule
        started = time.perf_counter()
        second = await attach(workers[1], "recover=1", token)
        recovery_ms = (time.perf_counter() - started) * 1000
        assert first.epoch != second.epoch
        await second.event(handler)
        assert second.text == "2"
        await first.socket.send(
            json.dumps(
                {"t": "repair", "version": 4, "epoch": first.epoch, "state_rev": first.revision}
            )
        )
        repair = await first.read("repair")
        assert re.search(
            r'id="recovery-count"[^>]*><pysx-slot[^>]*>1</pysx-slot>', cast("str", repair["html"])
        )
        workers[0].stop(kill=True)
        await first.socket.wait_closed()
        await second.event(handler)
        assert second.text == "3"
        third = await attach(workers[1], "recover=1", token)
        assert third.epoch != second.epoch

        return {
            "workers": 2,
            "separate_source_roots": True,
            "routing": "direct backend TCP override with preserved public Host/Origin",
            "recovered_on_other_worker": True,
            "copied_capsule_forks": True,
            "healthy_worker_after_peer_loss": True,
            "recovery_ms": recovery_ms,
            "recovery_samples": 1,
            "shared_files": [path.name for path in keys.iterdir()],
            "source_sha256": hashlib.sha256(body.encode()).hexdigest(),
        }
    finally:
        for socket in sockets:
            await socket.close()

        for worker in workers:
            worker.stop()


async def measure(count: int, directory: Path) -> dict[str, object]:
    started = time.perf_counter()
    cpu = time.process_time()
    observations = await history(count, directory)
    workers = await two_workers(directory / "workers")
    source = hashlib.sha256()

    for path in sorted((ROOT / "pysx").rglob("*")):
        if path.is_file() and path.suffix in {".py", ".js"}:
            source.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes())

    return {
        "result": "passed",
        "profile": "local",
        "reference_qualified": False,
        "environment": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "pysx": version("pysx"),
            "cryptography": version("cryptography"),
        },
        "source_sha256": source.hexdigest(),
        "limits": {
            "plaintext_bytes": MAX_PLAINTEXT,
            "capsule_bytes": MAX_CAPSULE,
            "slots": MAX_SLOTS,
            "nodes": MAX_NODES,
        },
        "history": observations,
        "two_workers": workers,
        "elapsed_seconds": time.perf_counter() - started,
        "runner_cpu_seconds": time.process_time() - cpu,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, choices=("local",))
    parser.add_argument("--sessions", required=True, type=int)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    if args.sessions < 1000:
        parser.error("local profile requires at least 1000 sessions")
    with tempfile.TemporaryDirectory(prefix="pysx-recovery-local-") as temporary:
        report = asyncio.run(measure(args.sessions, Path(temporary).resolve()))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    sys.stdout.write(json.dumps(report) + "\nBROWSER RECOVERY LOCAL VERIFICATION PASSED\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
