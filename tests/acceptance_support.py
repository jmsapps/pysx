"""Bounded subprocess lifecycle for direct and pytest acceptance entry points."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager, suppress
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence

    from websockets.asyncio.client import ClientConnection

    from pysx.runtime.wire import ServerMessage

ROOT = Path(__file__).resolve().parents[1]


async def receive(ws: ClientConnection, seconds: float = 5) -> ServerMessage:
    """Narrow decoded server JSON at the test transport boundary."""
    decoded: object = json.loads(await asyncio.wait_for(ws.recv(), seconds))

    if not isinstance(decoded, dict):
        raise AssertionError(f"expected a server message object: {decoded!r}")
    message = cast("dict[str, object]", decoded)
    assert message.get("t") in ("init", "patch", "mount", "result"), message

    # Detailed payload assertions remain in each acceptance case.
    return cast("ServerMessage", message)


STATUS_CONTROL_C_EXIT = 0xC000013A


def shutdown_status(code: int | None) -> int | None:
    """Normalise the exit status of a process asked to stop with Ctrl+C.

    Console control events reach an entire process group, and these commands run
    behind a `uv run` wrapper that installs no handler for them, so on Windows the
    wrapper reports STATUS_CONTROL_C_EXIT no matter how cleanly the pysx host
    underneath it drained. The host's own behaviour is asserted from its output;
    the wrapper's status carries no information on that platform.
    """

    if sys.platform == "win32" and code in (STATUS_CONTROL_C_EXIT, -STATUS_CONTROL_C_EXIT):
        return 0

    return code


def alive(pid: int) -> bool:
    """Report whether a pid is still running, without signalling it.

    `os.kill(pid, 0)` is the POSIX idiom and means something else entirely on
    Windows: anything other than a console control event is routed to
    `TerminateProcess`, so signal 0 asks to *kill* the process with exit code 0 and
    raises `OSError(87)` from `OpenProcess` when the pid is already gone. Probing
    liveness there needs a handle, not a signal.
    """

    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined, unused-ignore]
        # PROCESS_QUERY_LIMITED_INFORMATION: enough to read an exit code, and it
        # does not require rights to affect the process.
        handle = kernel32.OpenProcess(0x1000, False, pid)

        if not handle:
            return False

        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False

            # STILL_ACTIVE
            return code.value == 259
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    return True


def interrupt(proc: subprocess.Popen[str] | subprocess.Popen[bytes]) -> None:
    """Ask a child to shut down the way a console Ctrl+C would.

    Windows `Popen.send_signal` accepts only SIGTERM, CTRL_C_EVENT and
    CTRL_BREAK_EVENT, so sending SIGINT there raises `ValueError: Unsupported
    signal: 2` rather than stopping anything.

    **The child must have been spawned with CREATE_NEW_PROCESS_GROUP.**
    CTRL_BREAK_EVENT is delivered to an entire process group, so a child sharing
    the runner's group takes pytest down with it — silently, leaving a gate that
    simply stops producing output.
    """

    if sys.platform == "win32":
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        except OSError as error:
            raise OSError(f"{error}\n{_console_state(proc)}") from error

        return
    proc.send_signal(signal.SIGINT)


def _console_state(proc: subprocess.Popen[str] | subprocess.Popen[bytes]) -> str:
    """Report what the failing CTRL_BREAK_EVENT actually had to work with.

    `WinError 87` from `GenerateConsoleCtrlEvent` is ambiguous on its own: an already
    dead child, a child that does not lead its own process group, and a caller with no
    console all surface the same code. Guessing between them produced one wrong
    conclusion already, so the call site records the distinguishing facts instead.
    """

    if sys.platform != "win32":
        return ""
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined, unused-ignore]
    window = kernel32.GetConsoleWindow()
    buffer = (ctypes.c_uint32 * 256)()
    attached = kernel32.GetConsoleProcessList(buffer, 256)
    sharing = sorted(buffer[index] for index in range(min(attached, 256)))

    return (
        f"caller console window: {window!r}; "
        f"caller console holds {attached} process(es): {sharing}; "
        f"child pid {proc.pid} exit status {proc.poll()!r}; "
        f"child shares the caller's console: {proc.pid in sharing}"
    )


def stop_process(proc: subprocess.Popen[str], *, process_group: bool = True) -> None:
    """Stop the child and its server descendants, escalating after five seconds."""

    if sys.platform != "win32" and process_group:
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
    elif sys.platform == "win32" and proc.poll() is None:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=5,
        )
    elif proc.poll() is None:
        proc.terminate()

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if sys.platform != "win32" and process_group:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.wait(timeout=5)

    if proc.stdout is not None:
        proc.stdout.close()


@contextmanager
def ready_server(
    command: Sequence[str], timeout: float = 10, *, ready_banner: str = "pysx ready"
) -> Generator[subprocess.Popen[str]]:
    """Wait for the ready banner with a bounded reader and guaranteed cleanup."""
    owns_group = sys.platform != "win32" and os.environ.get("PYSX_ACCEPTANCE_GROUP") != "shared"
    proc = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=owns_group,
        # `interrupt` aims CTRL_BREAK_EVENT at this child, and Windows only delivers
        # that to a process that owns its group.
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
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

            if ready_banner in line:
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
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=sys.platform != "win32",
        env=environment,
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
