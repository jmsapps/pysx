"""Real runner fault cases: failures must be nonzero and release owned resources."""

import json
import os
import socket
import subprocess
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]


def browser_run(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", "tests/browser_runner.mjs", *arguments], cwd=ROOT,
        env={**os.environ, **environment}, capture_output=True, text=True,
        timeout=90, check=False,
    )


def test_pysx_3_st_1_local_dependencies() -> None:
    for name in ("browser.mjs", "browser_todos.mjs"):
        source = (ROOT / "tests" / name).read_text()
        assert 'from "playwright"' in source
        assert "../../tests/node_modules" not in source


@pytest.mark.parametrize("arguments", [("--phase", "PYSX-999"), ("--stage", "ST-999")])
def test_pysx_3_st_1_empty_selection(arguments: tuple[str, ...]) -> None:
    result = browser_run({}, *arguments)
    assert result.returncode != 0
    assert "empty browser selection" in result.stderr
    assert "VERIFICATION PASSED" not in result.stdout


def test_pysx_3_st_1_missing_engines(tmp_path: Path) -> None:
    result = browser_run({"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path)})
    assert result.returncode != 0
    assert "Executable doesn't exist" in result.stderr
    assert "VERIFICATION PASSED" not in result.stdout


@pytest.mark.acceptance
def test_pysx_3_st_1_occupied_port() -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        result = browser_run({"PYSX_BROWSER_PORT": str(port)})
        assert result.returncode != 0
        assert "EADDRINUSE" in result.stderr
        assert "VERIFICATION PASSED" not in result.stdout
        # The runner never kills or takes over a listener it does not own.
        assert occupied.getsockname()[1] == port


@pytest.mark.acceptance
def test_pysx_3_st_1_assertion_cleanup() -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    result = browser_run({"PYSX_BROWSER_PORT": str(port), "PYSX_BROWSER_FORCE_FAILURE": "1"})
    assert result.returncode != 0
    assert "forced assertion failure" in result.stderr
    assert "VERIFICATION PASSED" not in result.stdout
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
        probe.listen()


def grammar_run(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", "tests/grammar/runner.mjs", *arguments], cwd=ROOT,
        env={**os.environ, **environment}, capture_output=True, text=True,
        timeout=30, check=False,
    )


def test_pysx_3_st_2_token_assertions() -> None:
    result = grammar_run({}, "--phase", "PYSX-3", "--stage", "ST-2")
    assert result.returncode == 0, result.stderr
    assert "3 fixtures, 6 assertions" in result.stdout
    assert "GRAMMAR VERIFICATION PASSED: PYSX-3/ST-2" in result.stdout


def test_pysx_3_st_2_mutated_injection(tmp_path: Path) -> None:
    source = (ROOT / "editor/syntaxes/pysx.injection.tmLanguage.json").read_text()
    mutation = tmp_path / "mutation.json"
    mutation.write_text(source.replace("support.class.component.pysx", "support.class.broken"))
    result = grammar_run({"PYSX_INJECTION_GRAMMAR": str(mutation)})
    assert result.returncode != 0
    assert "scope assertion failed" in result.stderr
    assert "VERIFICATION PASSED" not in result.stdout


@pytest.mark.parametrize("failure", ["missing", "incompatible"])
def test_pysx_3_st_2_invalid_host(tmp_path: Path, failure: str) -> None:
    if failure == "incompatible":
        (tmp_path / "package.json").write_text(json.dumps({"publisher": "other", "name": "host"}))
    result = grammar_run({"PYSX_PYLANCE_EXTENSION": str(tmp_path)})
    assert result.returncode != 0
    assert "VERIFICATION PASSED" not in result.stdout
    assert ("ENOENT" if failure == "missing" else "incompatible host") in result.stderr


def test_pysx_3_st_2_empty_selection() -> None:
    result = grammar_run({}, "--phase", "PYSX-999")
    assert result.returncode != 0
    assert "empty grammar selection" in result.stderr


def editor_run(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", "editor/test/runner.mjs", *arguments], cwd=ROOT,
        env={**os.environ, **environment}, capture_output=True, text=True,
        # Above the runner's own budget: three sequential 30s `uv run` steps
        # precede any deadline it enforces itself.
        timeout=180, check=False,
    )


def test_pysx_3_st_3_discovery_and_fresh_build() -> None:
    result = editor_run({"PYSX_EDITOR_PROBE": "1"})
    assert result.returncode == 0, result.stderr
    decoded: object = json.loads(result.stdout)
    assert isinstance(decoded, dict)
    state = cast("dict[str, object]", decoded)
    assert state["root"] == str(ROOT) + os.sep
    assert isinstance(state["interpreter"], str)
    assert isinstance(state["workspace"], str)
    assert Path(state["interpreter"]).is_file()
    assert not Path(state["workspace"]).exists()
    assert "VERIFICATION PASSED" not in result.stdout


@pytest.mark.parametrize("missing", ["PYSX_PYLANCE_EXTENSION", "PYSX_VSCODE_EXECUTABLE"])
def test_pysx_3_st_3_missing_input(tmp_path: Path, missing: str) -> None:
    result = editor_run({missing: str(tmp_path / "absent")})
    assert result.returncode != 0
    assert "VERIFICATION PASSED" not in result.stdout
    assert ("ENOENT" if missing == "PYSX_PYLANCE_EXTENSION" else "missing VSCode") in result.stderr


def test_pysx_3_st_3_timeout_cleanup(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    result = editor_run({
        "PYSX_EDITOR_FORCE_TIMEOUT": "1", "PYSX_EDITOR_STATE_FILE": str(state_file),
    })
    assert result.returncode != 0
    assert "editor deadline exceeded" in result.stderr
    decoded: object = json.loads(state_file.read_text())
    assert isinstance(decoded, dict)
    state = cast("dict[str, object]", decoded)
    assert isinstance(state["workspace"], str)
    assert not Path(state["workspace"]).exists()
    pid = str(state["pid"])
    command = (
        ["ps", "-o", "stat=", "-p", pid] if os.name == "posix"
        else ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"]
    )
    process = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    if os.name == "posix":
        assert not process.stdout.strip() or process.stdout.strip().startswith("Z")
    else:
        assert pid not in process.stdout


def test_pysx_3_st_3_empty_selection() -> None:
    result = editor_run({}, "--stage", "ST-999")
    assert result.returncode != 0
    assert "empty editor selection" in result.stderr
