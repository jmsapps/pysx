"""Process-owning helpers shared by ownership and operational acceptance."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Self, cast

from websockets.asyncio.client import ClientConnection, connect

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType
    from typing import IO


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))

        return cast("int", listener.getsockname()[1])


def get(port: int, path: str = "/health") -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


@dataclass
class Worker:
    process: subprocess.Popen[str]
    port: int
    identifier: str
    log: IO[str]

    def stop(self, *, kill: bool = False) -> None:
        if self.process.poll() is None:
            self.process.kill() if kill else self.process.terminate()

            try:
                self.process.wait(timeout=7)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.log.close()


class Workers:
    def __init__(self, directory: Path, *, count: int = 2, workload: bool = False) -> None:
        self.directory = directory
        self.store = directory / "state.db"
        self.workers: list[Worker] = []
        self.count = count
        self.workload = workload
        self._lock = RLock()

    def start(
        self,
        *,
        port: int | None = None,
        schema: int = 1,
        migrate: bool = False,
        fresh: bool = False,
    ) -> Worker:
        with self._lock:
            return self._start(port=port, schema=schema, migrate=migrate, fresh=fresh)

    def _start(self, *, port: int | None, schema: int, migrate: bool, fresh: bool) -> Worker:
        selected = port or free_port()
        identifier = secrets.token_urlsafe(18)
        log = (self.directory / f"worker-{identifier}.log").open("w+")
        environment = dict(os.environ)
        environment.update(
            {
                "PYSX_FIXTURE_STORE": str(self.store),
                "PYSX_FIXTURE_WORKER": identifier,
                "PYSX_FIXTURE_SCHEMA": str(schema),
                "PYSX_FIXTURE_LEASE": "3" if self.workload else "1",
                "PYSX_FIXTURE_MIGRATE": "1" if migrate else "",
                "PYSX_FIXTURE_FRESH": "1" if fresh else "0",
            }
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "pysx.server",
                "--app",
                "tests.recovery_workload_fixture:host"
                if self.workload
                else "tests.worker_fixture:host",
                "--port",
                str(selected),
            ],
            env=environment,
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        worker = Worker(process, selected, identifier, log)
        self.workers.append(worker)
        deadline = time.monotonic() + 10

        while time.monotonic() < deadline and process.poll() is None:
            try:
                if get(selected)[0] == 200:
                    return worker
            except OSError, urllib.error.URLError:
                pass
            time.sleep(0.02)
        log.seek(0)

        raise RuntimeError("worker did not start: " + log.read())

    def __enter__(self) -> Self:
        try:
            for _ in range(self.count):
                self.start()
        except BaseException:
            self.close()

            raise

        return self

    def close(self) -> None:
        with self._lock:
            for worker in reversed(self.workers):
                worker.stop()

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _error: BaseException | None,
        _trace: TracebackType | None,
    ) -> None:
        self.close()


class Client:
    def __init__(self, socket: ClientConnection, browser: str, frame: dict[str, object]) -> None:
        self.socket = socket
        self.browser = browser
        self.reconstructed = frame["t"] == "init"
        self.epoch = str(frame["epoch"])
        self.revision = int(cast("int", frame["state_rev"]))
        self.resume = str(frame["resume"])
        self.recovery = str(frame["recovery"])
        self.owner = str(frame["owner"])
        self.sequence = 0
        self.html = str(frame["html"])
        self.handlers = re.findall(r'data-pysx-click="([^"]+)"', self.html)
        self.bindings = re.findall(r'data-pysx-binding="([^"]+)"', self.html)
        assert len(self.handlers) == 2

    @classmethod
    async def attach(
        cls, worker: Worker, *, previous: Client | None = None, user: str = "alice"
    ) -> Client:
        browser = previous.browser if previous is not None else secrets.token_urlsafe(32)
        socket = await connect(
            f"ws://127.0.0.1:{worker.port}/ws?protocol=3" + ("&resume=1" if previous else ""),
            additional_headers={"cookie": f"pysx_browser={browser}; fixture_user={user}"},
        )
        hello: dict[str, object] = {"t": "hello", "version": 3}

        if previous is not None:
            hello.update(
                {
                    "resume": previous.resume,
                    "recovery": previous.recovery,
                    "epoch": previous.epoch,
                    "state_rev": previous.revision,
                }
            )
        await socket.send(json.dumps(hello))

        try:
            frame = cast("dict[str, object]", json.loads(await asyncio.wait_for(socket.recv(), 5)))
            assert frame["t"] in ("init", "repair"), frame

            return cls(socket, browser, frame)
        except BaseException:
            await socket.close()

            raise

    async def event(
        self,
        *,
        slow: bool = False,
        wait: bool = True,
        handler: str | None = None,
        value: object = None,
    ) -> None:
        self.sequence += 1
        await self.socket.send(
            json.dumps(
                {
                    "t": "event",
                    "version": 3,
                    "epoch": self.epoch,
                    "state_rev": self.revision,
                    "seq": self.sequence,
                    "h": handler or self.handlers[int(slow)],
                    "v": value,
                }
            )
        )

        if wait:
            async with asyncio.timeout(5):
                while True:
                    frame = cast("dict[str, object]", json.loads(await self.socket.recv()))
                    self.revision = cast("int", frame["state_rev"])

                    if frame["t"] == "ack" and frame.get("seq") == self.sequence:
                        assert frame["status"] == "success"

                        return

    async def close(self) -> None:
        with suppress(OSError):
            await self.socket.close()
