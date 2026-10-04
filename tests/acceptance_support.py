"""Bounded subprocess lifecycle for direct and pytest acceptance entry points."""

import asyncio
import json
import os
import signal
import subprocess
import threading
from contextlib import contextmanager, suppress
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence

    from websockets.asyncio.client import ClientConnection

    from pysx.wire import ServerMessage

ROOT = Path(__file__).resolve().parents[1]


async def receive(ws: ClientConnection, seconds: float = 5) -> ServerMessage:
    """Narrow decoded server JSON at the test transport boundary."""
    decoded: object = json.loads(await asyncio.wait_for(ws.recv(), seconds))
    if not isinstance(decoded, dict):
        raise AssertionError(f"expected a server message object: {decoded!r}")
    message = cast("dict[str, object]", decoded)
    assert message.get("t") in ("init", "patch"), message
    # Detailed payload assertions remain in each acceptance case.
    return cast("ServerMessage", message)


def stop_process(proc: subprocess.Popen[str], *, process_group: bool = True) -> None:
    """Stop the child and its server descendants, escalating after five seconds."""
    if os.name == "posix" and process_group:
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
    elif os.name == "nt" and proc.poll() is None:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True, check=False, timeout=5,
        )
    elif proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix" and process_group:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.wait(timeout=5)
    if proc.stdout is not None:
        proc.stdout.close()


@contextmanager
def ready_server(command: Sequence[str], timeout: float = 10) -> Generator[subprocess.Popen[str]]:
    """Wait for the ready banner with a bounded reader and guaranteed cleanup."""
    owns_group = os.name == "posix" and os.environ.get("PYSX_ACCEPTANCE_GROUP") != "shared"
    proc = subprocess.Popen(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=owns_group,
    )
    lines: Queue[str | None] = Queue()
    assert proc.stdout is not None
    stdout = proc.stdout

    def read_lines() -> None:
        for line in stdout:
            lines.put(line)
        lines.put(None)

    reader = threading.Thread(target=read_lines, daemon=True)
    reader.start()
    timer = threading.Timer(timeout, lambda: lines.put(None))
    timer.start()
    output: list[str] = []
    try:
        while (line := lines.get()) is not None:
            output.append(line)
            if "pysx ready" in line:
                yield proc
                return
        raise RuntimeError("server did not start within deadline: " + "".join(output))
    finally:
        timer.cancel()
        stop_process(proc, process_group=owns_group)
        reader.join(timeout=5)


def run_suite(command: Sequence[str], banner: str, timeout: float = 30) -> str:
    """Run a suite; make failure and timeout diagnostic and clean its descendants."""
    environment = dict(os.environ)
    environment["PYSX_ACCEPTANCE_GROUP"] = "shared"
    proc = subprocess.Popen(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=os.name == "posix", env=environment,
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
        if proc.returncode != 0:
            raise AssertionError(f"acceptance exited {proc.returncode}: {output}")
        if banner not in output:
            raise AssertionError(f"missing {banner!r}: {output}")
        return output
    finally:
        stop_process(proc)
