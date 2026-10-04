"""Real headless suites plus subprocess failure and cleanup regressions."""

import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from acceptance_support import ready_server, run_suite


@pytest.mark.acceptance
@pytest.mark.parametrize(
    ("script", "banner"),
    [("acceptance.py", "ACCEPTANCE PASSED"), ("acceptance_todos.py", "TODOS ACCEPTANCE PASSED")],
)
def test_headless_acceptance(script: str, banner: str) -> None:
    run_suite([sys.executable, str(Path(__file__).with_name(script))], banner)


def test_pysx_2_st_2_suite_failure() -> None:
    with pytest.raises(AssertionError, match="acceptance exited 3"):
        run_suite([sys.executable, "-c", "raise SystemExit(3)"], "PASSED")


def test_pysx_2_st_2_suite_timeout() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        run_suite([sys.executable, "-c", "import time; time.sleep(30)"], "PASSED", timeout=0.1)


def test_pysx_2_st_2_missing_banner() -> None:
    with pytest.raises(AssertionError, match="missing"):
        run_suite([sys.executable, "-c", "pass"], "PASSED")


def test_pysx_2_st_2_ready_server_cleanup() -> None:
    with ready_server([
        sys.executable, "-c", "import time; print('pysx ready', flush=True); time.sleep(30)",
    ]) as proc:
        assert proc.poll() is None
    assert proc.poll() is not None


def test_pysx_2_st_2_ready_server_timeout() -> None:
    with pytest.raises(RuntimeError, match="within deadline"), ready_server([
        sys.executable, "-c", "import time; time.sleep(30)",
    ], timeout=0.1):
        pytest.fail("unready child yielded")


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_pysx_2_st_2_nested_server_cleanup(tmp_path: Path, failure: str) -> None:
    pid_file = tmp_path / "server.pid"
    # Reproduce the real suite/server hierarchy, including the shared process group.
    source = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, 'tests')\n"
        "from acceptance_support import ready_server\n"
        "with ready_server([sys.executable, '-c', "
        '"import time; print(\'pysx ready\', flush=True); time.sleep(30)"]'
        ") as server:\n"
        f"    Path({str(pid_file)!r}).write_text(str(server.pid))\n"
        + ("    raise SystemExit(3)\n" if failure == "exit" else "    time.sleep(30)\n")
    )
    expected = AssertionError if failure == "exit" else subprocess.TimeoutExpired
    with pytest.raises(expected):
        run_suite([sys.executable, "-c", source], "PASSED", timeout=1)
    pid = int(pid_file.read_text())
    command = (
        ["ps", "-o", "stat=", "-p", str(pid)] if os.name == "posix"
        else ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"]
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    if os.name == "posix":
        assert not result.stdout.strip() or result.stdout.strip().startswith("Z"), result.stdout
    else:
        assert str(pid) not in result.stdout, result.stdout


@pytest.mark.acceptance
@pytest.mark.parametrize("example", ["counter", "todos"])
@pytest.mark.parametrize("entry", ["console", "root-script"])
def test_pysx_2_st_4_example_commands(example: str, entry: str) -> None:
    command = (
        [sys.executable, "run_example.py"] if entry == "root-script"
        else [str(Path(sys.executable).with_name("example.exe" if os.name == "nt" else "example"))]
    )
    # Keep the smoke port separate from headless suites and the development server.
    port = 8754
    with (
        ready_server([*command, "run", example, "--port", str(port)]),
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response,
    ):
        assert response.status == 200
        assert "<!doctype html>" in response.read().decode().lower()
