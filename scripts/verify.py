"""The verification gate manifest. CI and local runs execute this same list.

Adding a gate here is the only supported way to add one: `tests/test_quality_configuration.py`
fails if `.github/workflows/quality.yml` grows a step that this file does not declare.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parent.parent
PYTHON_VERSION = "3.14.4"
BROWSER_ENGINES = ("chromium", "firefox", "webkit")
MEASUREMENT_MARKERS = ("PYSX EDITOR WAIT:", "PYSX EDITOR SAVE:")
PYTEST_TIMEOUT = 3600.0 if sys.platform == "win32" else 1200.0

BOOTSTRAP: tuple[tuple[str, ...], ...] = (
    ("uv", "python", "install", PYTHON_VERSION),
    ("uv", "sync", "--locked", "--python", PYTHON_VERSION),
)

ENTRYPOINT: tuple[str, ...] = ("uv", "run", "--project", ".", "python", "scripts/verify.py", "all")


@dataclass(frozen=True)
class Gate:
    tier: str
    name: str
    command: tuple[str, ...]
    needs_display: bool = False
    timeout: float = 1800


def _playwright_install() -> tuple[str, ...]:
    flags = ("--with-deps",) if sys.platform.startswith("linux") else ()

    install = ("npm", "--prefix", "tests", "exec", "--", "playwright", "install")

    return (*install, *flags, *BROWSER_ENGINES)


GATES: tuple[Gate, ...] = (
    Gate("setup", "npm-tests", ("npm", "--prefix", "tests", "ci")),
    Gate("setup", "npm-editor", ("npm", "--prefix", "editor", "ci")),
    Gate("setup", "playwright", _playwright_install()),
    Gate("setup", "vscode-host", ("npm", "--prefix", "editor", "run", "setup:host")),
    Gate("python", "lockfile", ("uv", "lock", "--check")),
    Gate(
        "python",
        "ruff",
        ("uv", "run", "--project", ".", "python", "-m", "pysx.tooling.quality", "ruff"),
    ),
    Gate(
        "python",
        "mypy",
        ("uv", "run", "--project", ".", "python", "-m", "pysx.tooling.quality", "mypy"),
    ),
    Gate(
        "python",
        "pyright",
        ("uv", "run", "--project", ".", "python", "-m", "pysx.tooling.quality", "pyright"),
    ),
    Gate(
        "python",
        "generator",
        ("uv", "run", "--project", ".", "python", "scripts/generate_native.py", "--check"),
    ),
    # Linux finishes this in about six minutes, so a gate still running at twenty
    # is hung rather than slow, and the remaining tiers still fit the job budget.
    # Windows runs the same suite several times slower, so it gets its own budget.
    Gate(
        "python",
        "pytest",
        ("uv", "run", "--project", ".", "pytest", "-q"),
        timeout=PYTEST_TIMEOUT,
    ),
    Gate("browser", "browser", ("npm", "--prefix", "tests", "run", "browser")),
    Gate("grammar", "grammar", ("npm", "--prefix", "tests", "run", "grammar")),
    Gate("editor", "editor", ("npm", "--prefix", "editor", "test"), needs_display=True),
)

TIERS: tuple[str, ...] = ("setup", "python", "browser", "grammar", "editor")
PROBE_GATES: tuple[Gate, ...] = (
    Gate(
        "diagnostic",
        "storage-source-fixtures",
        (
            "uv",
            "run",
            "--project",
            ".",
            "pytest",
            "-q",
            "tests/test_browser_recovery.py",
            "tests/test_source_map.py",
            "tests/test_template_import_usage.py",
            "tests/test_editor_config.py",
            "tests/test_playgrounds.py",
            "tests/test_deployment.py::test_operational_topology_probe_store_failure_drain_and_redaction",
            "tests/test_distribution.py::test_host_config_fresh_actual_wheel_user_app",
        ),
        timeout=300,
    ),
    Gate(
        "diagnostic",
        "startup-shutdown",
        (
            "uv",
            "run",
            "--project",
            ".",
            "pytest",
            "-q",
            "tests/test_hosts.py::test_host_config_shutdown_waits_for_startup_and_disposes_racing_get",
            "tests/test_server_limits.py::test_bound_sends_shutdown_cancels_stalled_startup",
            "tests/test_server_limits.py::test_bound_sends_real_signal_shutdown",
        ),
        timeout=60,
    ),
    Gate(
        "diagnostic",
        "ruff-fixtures",
        (
            "uv",
            "run",
            "--project",
            ".",
            "pytest",
            "-q",
            "tests/test_editor_distribution.py",
            "-k",
            "lint_qualification or ruff",
        ),
        timeout=180,
    ),
    Gate(
        "diagnostic",
        "lsp-projection",
        (
            "uv",
            "run",
            "--project",
            ".",
            "pytest",
            "-q",
            "-s",
            "tests/test_lsp.py",
            "tests/test_lsp_features.py",
            "tests/test_lsp_edits.py",
        ),
        timeout=600,
    ),
)
SELECTORS: dict[str, tuple[str, ...]] = {
    "all": TIERS,
    "gates": tuple(tier for tier in TIERS if tier != "setup"),
    **{tier: (tier,) for tier in TIERS},
    "probe": ("diagnostic",),
}


def environment() -> dict[str, str]:
    env = dict(os.environ)
    # Output is captured, so a gate that hangs is only diagnosable from what it
    # already wrote; buffering would discard exactly that.
    env["PYTHONUNBUFFERED"] = "1"
    # The suites read and write source containing emoji and Greek throughout. Python
    # picks the locale encoding for text IO, which is cp1252 on Windows and cannot
    # represent any of it, so the gates fix UTF-8 for every platform rather than
    # depending on whichever locale the runner happens to have.
    env["PYTHONUTF8"] = "1"
    managed = ROOT / ".vscode-test" / "extensions"

    if "PYSX_EXTENSIONS_DIR" not in env and managed.is_dir():
        env["PYSX_EXTENSIONS_DIR"] = str(managed)

    return env


def resolve(gate: Gate) -> tuple[str, ...]:
    needs_xvfb = (
        gate.needs_display
        and sys.platform.startswith("linux")
        and not os.environ.get("DISPLAY")
        and shutil.which("xvfb-run") is not None
    )

    command = ("xvfb-run", "-a", *gate.command) if needs_xvfb else gate.command

    return (_executable(command[0]), *command[1:])


def _executable(name: str) -> str:
    """Resolve a launcher to a real file.

    Windows ships npm and npx as `.cmd` shims, which `CreateProcess` cannot run
    from a bare name, so the gate has to name the file PATH would have picked.
    """

    return shutil.which(name) or name


def run(gate: Gate, *, stream: bool) -> tuple[bool, float, str]:
    command = resolve(gate)
    started = time.monotonic()

    # A gate that hangs would otherwise consume the whole job budget and report
    # nothing. Each gate owns a process group so the whole tree can be ended;
    # killing only the direct child leaves grandchildren holding the pipes, and
    # the wait that follows then blocks on the very hang it is handling.
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment(),
        stdout=subprocess.PIPE if not stream else None,
        stderr=subprocess.PIPE if not stream else None,
        text=True,
        # The gates run under PYTHONUTF8, so decode their pipes as UTF-8 rather than
        # the locale encoding: on Windows that is cp1252, and a byte it cannot decode
        # kills the reader thread and discards everything the gate had written.
        encoding="utf-8",
        errors="replace",
        start_new_session=sys.platform != "win32",
        # Named dynamically: the flag only exists in the Windows subprocess module.
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )

    try:
        stdout, stderr = process.communicate(timeout=gate.timeout)
    except subprocess.TimeoutExpired as expired:
        _end_tree(process)

        # A tree that outlives its own kill must still be reported as this gate
        # failing. Letting the second wait raise loses the gate name, every other
        # gate behind it, and whatever output the timed-out command had produced.
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            stdout = (expired.stdout or b"").decode(errors="replace")
            previous = (expired.stderr or b"").decode(errors="replace")
            stderr = f"{previous}\nprocess tree survived kill"
        elapsed = time.monotonic() - started

        return False, elapsed, f"timed out after {gate.timeout}s\n{stdout or ''}{stderr or ''}"
    elapsed = time.monotonic() - started

    return process.returncode == 0, elapsed, f"{stdout or ''}{stderr or ''}"


def _end_tree(process: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        subprocess.run(
            [_executable("taskkill"), "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=30,
        )

        return

    with suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    process.kill()


def _drain_watchdog() -> None:
    """Print and clear any hang report the gate's tests left behind.

    The report cannot travel on the gate's stdout: pytest captures stderr per test
    and never reports the capture of a test that does not finish. Relying on the
    uploaded artifact instead hid it twice, so it is printed here as well.
    """

    logs = Path(os.environ.get("PYSX_DEPLOYMENT_LOGS", "") or ROOT / ".pysx-logs")
    report = logs / "task-watchdog.log"

    try:
        if not report.is_file():
            return
        print(report.read_text(encoding="utf-8", errors="replace"), flush=True)
        report.unlink()
    except OSError as error:
        print(f"   | watchdog report unreadable: {error}", flush=True)


def _utf8_console() -> None:
    """Make this process able to print what its gates produced.

    Three encodings sit between a gate's output and the build log: what the child
    emits, how the pipes are decoded, and how this process writes to its own
    stdout. All three must be UTF-8, and the last one is the easiest to forget —
    the suites emit emoji, and a cp1252 console turns that into a crash that
    discards the whole report.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)

        if reconfigure is not None:
            cast("Callable[..., None]", reconfigure)(encoding="utf-8", errors="replace")


def main() -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selector", nargs="?", default="gates", choices=sorted(SELECTORS))
    parser.add_argument("--verbose", action="store_true", help="stream gate output as it runs")
    parser.add_argument("--list", action="store_true", help="print the selected gates and exit")
    args = parser.parse_args()

    if args.selector == "all" and os.environ.get("PYSX_CI_PROBE") == "1":
        args.selector = "probe"
    tiers = SELECTORS[args.selector]
    selected = (
        list(PROBE_GATES)
        if args.selector == "probe"
        else [gate for gate in GATES if gate.tier in tiers]
    )

    if args.selector == "probe":
        print(
            "DIAGNOSTIC PROBE ONLY: this does not qualify the full verification manifest",
            flush=True,
        )

    if args.list:
        for gate in selected:
            print(f"{gate.tier:8} {gate.name:12} {' '.join(resolve(gate))}")

        return 0
    failures: list[tuple[Gate, str]] = []
    label = "DIAGNOSTIC PROBE" if args.selector == "probe" else "VERIFICATION"

    for gate in selected:
        print(f"-> {gate.tier}/{gate.name}", flush=True)
        passed, elapsed, output = run(gate, stream=args.verbose)
        _drain_watchdog()

        if passed:
            print(f"   ok {gate.name} ({elapsed:.1f}s)", flush=True)

            for line in output.splitlines():
                if line.startswith(MEASUREMENT_MARKERS):
                    print(f"   | {line}", flush=True)

            continue
        print(f"   FAILED {gate.name} ({elapsed:.1f}s): {' '.join(resolve(gate))}", flush=True)

        if output:
            print(output, flush=True)
        failures.append((gate, output))

        if gate.tier == "setup":
            break

    if failures:
        names = ", ".join(gate.name for gate, _output in failures)
        print(f"{label} FAILED: {len(failures)} gate(s): {names}", flush=True)

        return 1
    print(f"{label} PASSED: {len(selected)} gates ({args.selector})", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
