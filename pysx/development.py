"""Opt-in local development supervisor; importing this module starts nothing."""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import json
import math
import os
import secrets
import signal
import socket
import sys
import time
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response
from websockets.typing import Origin

from .hosting import request_from_headers
from .security import AccessDenied, SecurityPolicy
from .wire import JSONValue

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

    from websockets.asyncio.client import ClientConnection
    from websockets.http11 import Request


_EXCLUDED = frozenset({".git", ".venv", "node_modules", "__pycache__", "dist", "build"})
_SUFFIXES = frozenset({".py", ".css", ".pysx", ".html", ".txt"})
_HOP = frozenset({"connection", "upgrade", "keep-alive", "transfer-encoding", "content-length"})
_CLIENT = b"""
;(() => {
  const script = document.currentScript;
  if (!script) return;
  const source = new URL(script.src);
  let generation = source.searchParams.get('pysx-dev');
  if (!generation) return;
  let stopped = false;
  addEventListener('pagehide', () => { stopped = true; }, {once: true});
  function apply(status) {
    if (String(status.generation) !== generation) {
      if (status.fresh_generation > Number(generation)) { location.reload(); return; }
      generation = String(status.generation);
    }
    let display = document.getElementById('pysx-dev-error');
    if (status.error) {
      if (!display) {
        display = document.createElement('pre');
        display.id = 'pysx-dev-error'; display.setAttribute('role', 'alert');
        document.body.append(display);
      }
      display.textContent = status.error;
    } else if (display) display.remove();
    window.dispatchEvent(new CustomEvent('pysx:development', {detail: status}));
  }
  async function poll() {
    try {
      const response = await fetch('/__pysx_dev__/status', {cache: 'no-store'});
      if (response.ok) apply(await response.json());
    } catch (_) { /* A stopped supervisor must not destroy the current UI. */ }
    finally { if (!stopped) setTimeout(poll, 250); }
  }
  function live() {
    const url = new URL('/__pysx_dev__/events', location.href);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    let socket;
    try { socket = new WebSocket(url); } catch (_) { void poll(); return; }
    socket.addEventListener('message', event => {
      try { apply(JSON.parse(event.data)); } catch (_) { /* ignore a malformed frame */ }
    });
    socket.addEventListener('error', () => socket.close());
    socket.addEventListener('close', () => { if (!stopped) void poll(); });
  }
  live();
})();
"""


@dataclass(frozen=True)
class WatchConfig:
    app: str
    roots: tuple[Path, ...]
    interval: float = 0.05
    debounce: float = 0.03
    timeout: float = 10
    max_files: int = 10000
    inspect: bool = False
    preserve: bool = True
    digests: dict[str, tuple[int, int, str]] = field(
        default_factory=lambda: dict[str, tuple[int, int, str]](), compare=False, repr=False
    )

    def __post_init__(self) -> None:
        if not self.app or not self.roots:
            raise ValueError("watch requires an app and at least one source root")

        for name in ("interval", "debounce", "timeout"):
            value = getattr(self, name)

            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"watch {name} must be finite and positive")

        if self.max_files < 1:
            raise ValueError("watch max_files must be positive")

        if any(not root.is_dir() for root in self.roots):
            raise ValueError("watch roots must be existing directories")

    def snapshot(self) -> dict[str, str]:
        found: dict[str, str] = {}
        fresh: dict[str, tuple[int, int, str]] = {}

        def walk(root: Path) -> None:
            for path in sorted(root.iterdir()):
                if path.is_symlink() or path.name.startswith(".") or path.name in _EXCLUDED:
                    continue

                if path.is_dir():
                    walk(path)
                elif path.suffix in _SUFFIXES:
                    if len(found) >= self.max_files:
                        raise ValueError(f"watch exceeds {self.max_files} source files")

                    try:
                        status = path.stat()
                        key = str(path.resolve())
                        stamp = (status.st_mtime_ns, status.st_size)
                        known = self.digests.get(key)
                        digest = (
                            known[2]
                            if known is not None and known[:2] == stamp
                            else hashlib.sha256(path.read_bytes()).hexdigest()
                        )
                    except FileNotFoundError, NotADirectoryError:
                        continue
                    found[key] = digest
                    fresh[key] = (*stamp, digest)

        for root in self.roots:
            walk(root)
        self.digests.clear()
        self.digests.update(fresh)

        return found


class _HTTPConnection(ServerConnection):
    """Preserve HTTP methods before the WebSocket library's GET-only parser."""

    method: str = "GET"
    first_line: bytes = b""
    reading_line: bool = True

    def data_received(self, data: bytes) -> None:
        if not self.reading_line:
            super().data_received(data)

            return
        self.first_line += data

        if len(self.first_line) > 8192 and b"\r\n" not in self.first_line:
            self.transport.close()

            return

        if b"\r\n" not in self.first_line:
            return
        method, separator, rest = self.first_line.partition(b" ")
        self.method = method.decode("ascii", errors="replace")
        self.reading_line = False
        self.first_line = b""
        super().data_received(b"GET" + separator + rest)


def _request(
    port: int,
    path: str,
    headers: list[tuple[str, str]],
    method: str = "GET",
    timeout: float = 2,
) -> tuple[int, Headers, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)

    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)

        for name, value in headers:
            if name.lower() not in _HOP:
                connection.putheader(name, value)
        connection.putheader("Connection", "close")
        connection.endheaders()
        result = connection.getresponse()
        body = result.read(16 * 1024 * 1024 + 1)

        if len(body) > 16 * 1024 * 1024:
            raise ValueError("development response exceeds 16 MiB")

        return result.status, Headers(result.getheaders()), body
    finally:
        connection.close()


@dataclass
class Worker:
    process: asyncio.subprocess.Process
    port: int
    control_port: int
    token: str
    info: dict[str, JSONValue] = field(default_factory=lambda: dict[str, JSONValue]())
    output: str = ""
    reader: asyncio.Task[None] | None = None
    retiring: bool = False

    def control(self) -> connect:
        return connect(
            f"ws://127.0.0.1:{self.control_port}",
            proxy=None,
            additional_headers={"Authorization": "Bearer " + self.token},
            open_timeout=2,
            close_timeout=1,
            max_size=16 * 1024 * 1024,
        )

    @staticmethod
    async def exchange(connection: ClientConnection, message: dict[str, JSONValue]) -> JSONValue:
        await connection.send(json.dumps(message))
        result: object = json.loads(await connection.recv())

        if not isinstance(result, dict):
            raise RuntimeError("invalid worker control response")

        reply = cast("dict[str, JSONValue]", result)

        if "error" in reply:
            raise RuntimeError(str(reply["error"]))

        return reply.get("result")

    async def command(self, message: dict[str, JSONValue]) -> JSONValue:
        async with self.control() as connection:
            return await self.exchange(connection, message)

    async def drain(self) -> None:
        assert self.process.stdout is not None

        while chunk := await self.process.stdout.read(4096):
            self.output = (self.output + chunk.decode(errors="replace"))[-16000:]

    async def close(self) -> None:
        if self.process.returncode is None:
            with suppress(ProcessLookupError):
                if os.name == "posix":
                    os.killpg(self.process.pid, signal.SIGTERM)
                else:
                    self.process.terminate()

            try:
                async with asyncio.timeout(5):
                    await self.process.wait()
            except TimeoutError:
                with suppress(ProcessLookupError):
                    if os.name == "posix":
                        os.killpg(self.process.pid, signal.SIGKILL)
                    else:
                        self.process.kill()
                await self.process.wait()

        if os.name == "posix":
            # An import can start descendants before it raises. The leader may
            # already have exited while those descendants still own its pipes.
            with suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)

        if self.reader is not None:
            try:
                async with asyncio.timeout(1):
                    await self.reader
            except TimeoutError:
                self.reader.cancel()


@dataclass
class Supervisor:
    config: WatchConfig
    active: Worker | None = None
    generation: int = 0
    fresh_generation: int = 0
    error: str | None = None
    workers: list[Worker] = field(default_factory=lambda: list[Worker]())
    stopping: asyncio.Event = field(default_factory=asyncio.Event)
    traffic: asyncio.Event = field(default_factory=asyncio.Event)
    retained: dict[str, tuple[float, JSONValue]] = field(
        default_factory=lambda: dict[str, tuple[float, JSONValue]]()
    )
    reload_seconds: float = 0
    import_seconds: float = 0
    analysis_seconds: float = 0
    analysis_version: int = 0
    analysis_error: str | None = None
    analysis_paths: set[str] = field(default_factory=set[str])
    analysis_report: dict[str, JSONValue] = field(default_factory=lambda: {"files": []})
    analysis_task: asyncio.Task[None] | None = None
    analysis_worker: Worker | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    carried_slots: int = 0
    dropped_slots: int = 0
    bounded: JSONValue = None
    stages: dict[str, JSONValue] = field(default_factory=dict[str, JSONValue])
    detected_at: float = 0

    def __post_init__(self) -> None:
        self.traffic.set()

    async def status_report(self) -> dict[str, JSONValue]:
        report = self.status()
        worker = self.active

        if worker is not None:
            with suppress(OSError, RuntimeError, TimeoutError, ConnectionClosed):
                async with asyncio.timeout(2):
                    local = await worker.command({"op": "local_report"})

                if isinstance(local, dict):
                    report.update(local)

        return report

    def notify(self) -> None:
        self.changed.set()
        self.changed = asyncio.Event()

    def status(self) -> dict[str, JSONValue]:
        return {
            "generation": self.generation,
            "fresh_generation": self.fresh_generation,
            "error": self.error,
            "workers": [worker.process.pid for worker in self.workers],
            "reload_seconds": self.reload_seconds,
            "import_seconds": self.import_seconds,
            "carried_slots": self.carried_slots,
            "dropped_slots": self.dropped_slots,
            "bounded": self.bounded,
            "stages": self.stages,
            "detected_at": self.detected_at,
        }

    def request_analysis(self, paths: list[str], *, changed: bool = False) -> None:
        if not self.config.inspect:
            return
        before = self.analysis_paths.copy()

        for filename in paths:
            path = Path(filename)

            if path.suffix == ".py" and path.is_file():
                self.analysis_paths.add(str(path.resolve()))
            else:
                self.analysis_paths.discard(str(path.resolve()))

        if before == self.analysis_paths and not changed:
            return
        self.analysis_version += 1

        if self.analysis_task is None or self.analysis_task.done():
            self.analysis_task = asyncio.create_task(self.analyse())

    async def analyse(self) -> None:
        while True:
            version = self.analysis_version
            started = asyncio.get_running_loop().time()
            paths = sorted(self.analysis_paths)

            try:
                if len(paths) > 1024:
                    raise ValueError("source analysis exceeds 1024 files")
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "pysx.development_diagnostics",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=os.name == "posix",
                    env={
                        **os.environ,
                        "PYSX_DEV_ROOTS": json.dumps([str(root) for root in self.config.roots]),
                    },
                )
                self.analysis_worker = Worker(process, 0, 0, "")

                try:
                    async with asyncio.timeout(self.config.timeout):
                        output, errors = await process.communicate(json.dumps(paths).encode())

                    if process.returncode != 0:
                        raise RuntimeError(errors.decode(errors="replace")[-2000:])
                    result = cast("dict[str, JSONValue]", json.loads(output))

                    if result.get("error"):
                        raise RuntimeError(str(result["error"]))

                    if version == self.analysis_version:
                        self.analysis_report = result
                        self.analysis_error = None
                finally:
                    await self.analysis_worker.close()
                    self.analysis_worker = None
            except (OSError, ValueError, RuntimeError, TimeoutError) as error:
                if version == self.analysis_version:
                    self.analysis_error = f"{type(error).__name__}: {error}"[-2000:]
            self.analysis_seconds = asyncio.get_running_loop().time() - started

            if version == self.analysis_version:
                return

    async def candidate(self) -> Worker:
        started = time.perf_counter()
        with socket.socket() as listener, socket.socket() as control_listener:
            listener.bind(("127.0.0.1", 0))
            control_listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            control_port = control_listener.getsockname()[1]
        token = secrets.token_urlsafe(32)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pysx.server",
            "--app",
            self.config.app,
            "--port",
            str(port),
            "--dev",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=os.name == "posix",
            env={
                **os.environ,
                "PYSX_DEV_CONTROL_PORT": str(control_port),
                "PYSX_DEV_CONTROL_TOKEN": token,
                "PYSX_DEV_INSPECT": "1" if self.config.inspect else "0",
                "PYSX_DEV_ROOTS": json.dumps([str(root) for root in self.config.roots]),
            },
        )
        worker = Worker(process, port, control_port, token)
        self.workers.append(worker)
        worker.reader = asyncio.create_task(worker.drain())

        try:
            async with asyncio.timeout(self.config.timeout):
                while process.returncode is None:
                    prefix = f"pysx ready -> http://127.0.0.1:{port}"
                    ready = next(
                        (line for line in worker.output.splitlines() if line.startswith(prefix)),
                        None,
                    )

                    if ready is not None:
                        self.stages["startup_seconds"] = time.perf_counter() - started
                        probe_started = time.perf_counter()
                        info = await worker.command({"op": "probe", "render": self.active is None})
                        self.stages["initial_probe_seconds"] = time.perf_counter() - probe_started

                        if not isinstance(info, dict):
                            raise RuntimeError("invalid candidate metadata")
                        worker.info = info

                        return worker
                    await asyncio.sleep(0.01)

                raise RuntimeError(f"candidate exited {process.returncode}")
        except BaseException as error:
            await worker.close()
            self.workers.remove(worker)

            if isinstance(error, (OSError, RuntimeError, TimeoutError)):
                raise RuntimeError(f"{type(error).__name__}: {error}\n{worker.output}") from error

            raise

    async def replace(self) -> None:
        candidate: Worker | None = None
        adopted = False
        started = asyncio.get_running_loop().time()
        self.stages = {}
        self.stages["quiet_seconds"] = (
            max(0, time.time() - self.detected_at) if self.detected_at else 0
        )

        try:
            candidate = await self.candidate()
            duration = candidate.info.get("import_seconds")
            self.import_seconds = float(duration) if isinstance(duration, (int, float)) else 0
            sources = candidate.info.get("sources")

            if isinstance(sources, list):
                self.request_analysis([path for path in sources if isinstance(path, str)])
            previous = self.active
            mode = "fresh"
            validated = previous is None
            preflight_started = time.perf_counter()
            self.traffic.clear()

            async with AsyncExitStack() as transaction:
                async with asyncio.timeout(self.config.timeout):
                    if (
                        previous is not None
                        and candidate.info.get("durable")
                        and previous.info.get("durable")
                        and all(
                            candidate.info.get(key) == previous.info.get(key)
                            for key in ("application", "store", "base_path")
                        )
                    ):
                        connection = await transaction.enter_async_context(previous.control())
                        rows = await previous.exchange(connection, {"op": "freeze"})

                        if not isinstance(rows, list):
                            raise RuntimeError("invalid development snapshots")
                        now = asyncio.get_running_loop().time()
                        retained = {
                            key: value for key, value in self.retained.items() if value[0] > now
                        }

                        for row in rows:
                            if isinstance(row, dict) and isinstance(row.get("identity"), list):
                                retained[json.dumps(row["identity"])] = (now + 30, row)

                        if len(retained) > 256:
                            raise RuntimeError(
                                "development session limit (256); wait for retention expiry"
                            )
                        combined = [row for _, row in retained.values()]

                        if len(json.dumps(combined).encode()) > 16 * 1024 * 1024:
                            raise RuntimeError("development snapshots exceed 16 MiB")
                        mode_value = await candidate.command({"op": "validate", "rows": combined})
                        validated = True

                        if mode_value not in ("hot", "fresh"):
                            raise RuntimeError("invalid development recovery decision")
                        mode = str(mode_value)
                        await (await connection.ping())
                        self.retained = retained if mode == "hot" else {}
                    else:
                        self.retained.clear()

                        if (
                            previous is not None
                            and self.config.preserve
                            and previous.info.get("local")
                            and candidate.info.get("local")
                            and candidate.info.get("base_path") == previous.info.get("base_path")
                        ):
                            connection = await transaction.enter_async_context(previous.control())
                            frozen_at = time.perf_counter()
                            carried = await previous.exchange(connection, {"op": "freeze_local"})
                            self.stages["snapshot_seconds"] = time.perf_counter() - frozen_at

                            if isinstance(carried, dict):
                                self.bounded = carried.get("bounded")
                                held = carried.get("rows")
                                self.dropped_slots = int(cast("int", carried.get("dropped", 0)))

                                if isinstance(held, list):
                                    self.carried_slots = sum(
                                        sum(
                                            len(cast("dict[str, object]", scope))
                                            for scope in cast(
                                                "dict[str, object]", row.get("scopes", {})
                                            ).values()
                                        )
                                        for row in held
                                        if isinstance(row, dict)
                                    )
                                    restore_at = time.perf_counter()
                                    seeded = await candidate.command(
                                        {"op": "seed_local", "rows": held}
                                    )
                                    self.stages["restore_preflight_seconds"] = (
                                        time.perf_counter() - restore_at
                                    )
                                    validated = True

                                    if seeded == "seeded" and held:
                                        mode = "hot"

                    if not validated:
                        await candidate.command({"op": "probe"})

                self.stages["preflight_seconds"] = time.perf_counter() - preflight_started

                # Validation may roll back; retirement is the commit point and
                # has its own process/pipe deadlines rather than a rollback timer.
                if previous is not None:
                    retired_at = time.perf_counter()
                    previous.retiring = True
                    await previous.close()
                    self.stages["retirement_seconds"] = time.perf_counter() - retired_at

            self.active = candidate
            self.generation += 1

            if mode == "fresh":
                self.fresh_generation = self.generation
            self.error = None
            adopted = True

            if previous is not None:
                self.workers.remove(previous)
        except Exception as error:
            self.error = f"{type(error).__name__}: {error}"
            self.traffic.set()
        finally:
            if candidate is not None and not adopted:
                await candidate.close()
                self.workers.remove(candidate)
            self.traffic.set()
            self.reload_seconds = asyncio.get_running_loop().time() - started
            self.notify()

    async def watch(self, snapshot: dict[str, str]) -> None:
        changed: float | None = None
        loop = asyncio.get_running_loop()

        while not self.stopping.is_set():
            await asyncio.sleep(
                self.config.interval
                if changed is None
                else max(0.0, self.config.debounce - (loop.time() - changed))
            )

            try:
                current = await asyncio.to_thread(self.config.snapshot)
            except (OSError, ValueError) as error:
                self.error = str(error)

                continue

            if current != snapshot:
                self.detected_at = time.time()
                self.request_analysis(
                    [
                        path
                        for path in current.keys() | snapshot.keys()
                        if current.get(path) != snapshot.get(path)
                    ],
                    changed=True,
                )
                changed = loop.time()
                snapshot = current
            elif changed is not None and loop.time() - changed >= self.config.debounce:
                changed = None
                await self.replace()

    async def http(self, _: ServerConnection, request: Request) -> Response | None:
        response = await self._http(_, request)

        if response is not None and isinstance(_, _HTTPConnection) and _.method == "HEAD":
            response.body = b""

        return response

    async def _http(self, connection: ServerConnection, request: Request) -> Response | None:
        try:
            context = request_from_headers(
                request.path, list(request.headers.raw_items()), peer="127.0.0.1"
            )
            SecurityPolicy().check_network(context, websocket="upgrade" in request.headers)
        except ValueError, AccessDenied:
            return self.response(403, b"development server accepts local origins only")

        method = connection.method if isinstance(connection, _HTTPConnection) else "GET"

        if method not in ("GET", "HEAD"):
            response = self.response(405, b"method not allowed")
            response.headers["Allow"] = "GET, HEAD"

            return response

        if method == "GET" and request.headers.get("Upgrade", "").lower() == "websocket":
            return None
        path = urlsplit(request.path).path

        if path == "/__pysx_dev__/status":
            return self.response(
                200, json.dumps(await self.status_report()).encode(), "application/json"
            )

        if path == "/__pysx_dev__/cold":
            worker = self.active

            if worker is None:
                return self.response(503, b"no active worker")

            with suppress(OSError, RuntimeError, TimeoutError, ConnectionClosed):
                async with asyncio.timeout(2):
                    await worker.command({"op": "seed_local", "rows": []})
            self.carried_slots = 0
            self.dropped_slots = 0

            return self.response(200, b"cold")

        if path == "/__pysx_dev__/inspect":
            if not self.config.inspect or self.active is None:
                return self.response(404, b"inspector disabled")

            try:
                async with asyncio.timeout(2):
                    report = await self.active.command(
                        {"op": "inspect", "headers": dict(context.headers)}
                    )
            except OSError, RuntimeError, TimeoutError, ConnectionClosed:
                return self.response(503, b"inspector temporarily unavailable")

            if not isinstance(report, dict) or report.get("status") != 200:
                return self.response(403, b"inspector access denied")
            report["analysis"] = {
                "version": self.analysis_version,
                "pending": self.analysis_task is not None and not self.analysis_task.done(),
                "error": self.analysis_error,
                "seconds": self.analysis_seconds,
                **self.analysis_report,
            }

            return self.response(200, json.dumps(report).encode(), "application/json")
        await self.traffic.wait()
        worker = self.active

        if worker is None:
            return self.response(503, (self.error or "waiting for a valid application").encode())

        try:
            status, headers, body = await asyncio.to_thread(
                _request,
                worker.port,
                request.path,
                list(request.headers.raw_items()),
                method,
                self.config.timeout,
            )
        except (OSError, http.client.HTTPException, ValueError) as error:
            return self.response(502, str(error).encode())

        length = int(headers.get("Content-Length", str(len(body))))

        if path.endswith("/client.js") and status == 200:
            body += _CLIENT
            length += len(_CLIENT)

            if self.config.inspect:
                inspector = (Path(__file__).parent / "static" / "development.js").read_bytes()
                body += inspector
                length += len(inspector)
        elif headers.get("content-type", "").startswith("text/html") and status == 200:
            inspection = "&pysx-inspect=1" if self.config.inspect else ""
            body = body.replace(
                b'/client.js" defer',
                f'/client.js?pysx-dev={self.generation}{inspection}" defer'.encode(),
                1,
            )
        clean = Headers(
            (name, value)
            for name, value in headers.raw_items()
            if name.lower() not in _HOP | {"etag", "cache-control"}
        )

        if method != "HEAD":
            clean["Content-Length"] = str(len(body))
        elif not headers.get("content-type", "").startswith("text/html"):
            clean["Content-Length"] = str(length)
        clean["Cache-Control"] = "no-store"

        return Response(status, HTTPStatus(status).phrase, clean, body)

    @staticmethod
    def response(
        status: int, body: bytes, content_type: str = "text/plain; charset=utf-8"
    ) -> Response:
        return Response(
            status,
            HTTPStatus(status).phrase,
            Headers(
                {
                    "Content-Type": content_type,
                    "Content-Length": str(len(body)),
                    "Cache-Control": "no-store",
                }
            ),
            body,
        )

    async def events(self, client: ServerConnection) -> None:
        with suppress(ConnectionClosed):
            while not self.stopping.is_set():
                pending = self.changed
                await client.send(json.dumps(await self.status_report()))
                waiting: list[asyncio.Task[object]] = [
                    asyncio.create_task(pending.wait()),
                    asyncio.create_task(self.stopping.wait()),
                    asyncio.create_task(client.wait_closed()),
                ]

                try:
                    done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

                    if waiting[-1] in done:
                        return
                finally:
                    for task in waiting:
                        task.cancel()
                    await asyncio.gather(*waiting, return_exceptions=True)

    async def websocket(self, client: ServerConnection) -> None:
        if client.request is not None and (
            urlsplit(client.request.path).path == "/__pysx_dev__/events"
        ):
            await self.events(client)

            return
        await self.traffic.wait()
        worker = self.active

        if worker is None or client.request is None:
            await client.close(1013, "application unavailable")

            return
        headers = [
            (name, value)
            for name, value in client.request.headers.raw_items()
            if name.lower() not in _HOP | {"host", "origin"}
            and not name.lower().startswith("sec-websocket-")
        ]

        try:
            async with connect(
                f"ws://{client.request.headers['Host']}{client.request.path}",
                host="127.0.0.1",
                port=worker.port,
                proxy=None,
                origin=Origin(
                    client.request.headers.get("Origin", f"http://{client.request.headers['Host']}")
                ),
                additional_headers=headers,
                max_size=1024 * 1024,
                close_timeout=1,
            ) as upstream:
                async def forward() -> None:
                    try:
                        async for message in client:
                            await self.traffic.wait()

                            if self.active is not worker:
                                return
                            await upstream.send(message)
                    finally:
                        await upstream.close()

                sending = asyncio.create_task(forward())

                try:
                    async for message in upstream:
                        await client.send(message)
                finally:
                    sending.cancel()
                    with suppress(asyncio.CancelledError, ConnectionClosed):
                        await sending
                    await client.close(
                        4000 if worker.retiring else 1012,
                        "development reload"
                        if worker.retiring
                        else "development worker unavailable",
                    )
        except OSError, ConnectionClosed:
            await client.close(1012, "development worker unavailable")

    async def run(self, host: str, port: int) -> None:
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("watch is local development only; use a loopback host")
        loop = asyncio.get_running_loop()
        installed: list[
            tuple[signal.Signals, int | Callable[[int, FrameType | None], object] | None]
        ] = []

        for name in (signal.SIGINT, signal.SIGTERM):
            installed.append((name, signal.getsignal(name)))

            try:
                loop.add_signal_handler(name, self.stopping.set)
            except NotImplementedError:
                signal.signal(
                    name, lambda _signal, _frame: loop.call_soon_threadsafe(self.stopping.set)
                )
        watching: asyncio.Task[None] | None = None

        try:
            snapshot = await asyncio.to_thread(self.config.snapshot)
            entry = self.config.app.partition(":")[0].replace(".", "/") + ".py"
            self.request_analysis([str(root / entry) for root in self.config.roots])
            retirement_seconds = 6
            swap_window = 2 * self.config.timeout + retirement_seconds
            # Bind before spawning, so occupied ports cannot leak a worker.
            async with serve(
                self.websocket,
                host,
                port,
                process_request=self.http,
                create_connection=_HTTPConnection,
                open_timeout=swap_window,
                close_timeout=1,
            ) as server:
                try:
                    await self.replace()
                    watching = asyncio.create_task(self.watch(snapshot))
                    print(f"pysx ready -> http://{host}:{port} (watch)", flush=True)  # noqa: T201
                    await self.stopping.wait()
                finally:
                    # HTTP clients needn't consume rejected responses, and raw TCP
                    # peers may never send headers. Neither may hold shutdown open.
                    self.stopping.set()
                    self.notify()
                    server.close()
                    server.server.abort_clients()
        finally:
            if watching is not None:
                watching.cancel()
                with suppress(asyncio.CancelledError):
                    await watching

            if self.analysis_task is not None:
                self.analysis_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.analysis_task

            for worker in self.workers[:]:
                await worker.close()
                self.workers.remove(worker)

            for name, previous in installed:
                with suppress(NotImplementedError):
                    loop.remove_signal_handler(name)
                signal.signal(name, previous)


def run(config: WatchConfig, host: str, port: int) -> None:
    """Run explicitly; there are no watcher, thread or child import side effects."""
    # Fail invalid or oversized roots before claiming readiness.
    config.snapshot()
    asyncio.run(Supervisor(config).run(host, port))
