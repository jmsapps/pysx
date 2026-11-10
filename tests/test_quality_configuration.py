"""Check the repository's strict quality configuration and its verification manifest."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scripts.verify import BOOTSTRAP, ENTRYPOINT, GATES, PROBE_GATES, SELECTORS, TIERS, Gate, main

ROOT = Path(__file__).resolve().parents[1]
RUN_STEP = re.compile(r"^\s*- run: (.+)$", re.MULTILINE)


def test_probe_repetition_keeps_setup_once_and_preserves_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str] = []

    def fake_run(gate: Gate, *, stream: bool = False) -> tuple[bool, float, str]:
        assert not stream
        seen.append(gate.name)
        failed = gate.name == "editor-client" and seen.count(gate.name) == 1

        return not failed, 0.0, "first sample failed" if failed else ""

    monkeypatch.setenv("PYSX_CI_PROBE_GATE", "editor-client")
    monkeypatch.setenv("PYSX_CI_PROBE_REPETITIONS", "3")
    monkeypatch.setattr(sys, "argv", ["verify", "probe"])
    monkeypatch.setattr("scripts.verify.run", fake_run)
    monkeypatch.setattr("scripts.verify._drain_watchdog", lambda: None)
    assert main() == 1
    assert seen.count("npm-editor") == seen.count("vscode-host") == 1
    assert seen.count("editor-client") == 3
    output = capsys.readouterr().out
    assert "sample 3/3" in output
    assert "DIAGNOSTIC PROBE FAILED" in output


@pytest.mark.parametrize("value", ["0", "21", "invalid"])
def test_probe_repetition_rejects_invalid_bounds(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PYSX_CI_PROBE_GATE", "worker-ownership")
    monkeypatch.setenv("PYSX_CI_PROBE_REPETITIONS", value)
    monkeypatch.setattr(sys, "argv", ["verify", "probe", "--list"])

    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 2


def test_full_manifest_ignores_probe_repetition(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PYSX_CI_PROBE_REPETITIONS", "invalid")
    monkeypatch.setattr(sys, "argv", ["verify", "gates", "--list"])
    assert main() == 0
    assert len(capsys.readouterr().out.splitlines()) == sum(gate.tier != "setup" for gate in GATES)


def test_playwright_setup_identifies_dependency_and_download_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.verify import BROWSER_ENGINES, playwright_setup

    prefix = ("npm", "--prefix", "tests", "exec", "--", "playwright")
    monkeypatch.setattr(sys, "platform", "linux")
    linux = playwright_setup()
    assert [(gate.name, gate.command) for gate in linux] == [
        ("playwright-deps", (*prefix, "install-deps", *BROWSER_ENGINES)),
        ("playwright", (*prefix, "install", *BROWSER_ENGINES)),
    ]

    for platform in ("win32", "darwin"):
        monkeypatch.setattr(sys, "platform", platform)
        assert [gate.command for gate in playwright_setup()] == [
            (*prefix, "install", *BROWSER_ENGINES)
        ]


def test_strict_scope() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    tools = config["tool"]
    assert tools["mypy"]["strict"] is True
    assert tools["mypy"]["files"] == ["."]
    assert tools["mypy"]["python_version"] == "3.14"
    assert tools["pyright"]["include"] == ["."]
    assert tools["pyright"]["typeCheckingMode"] == "strict"
    assert tools["pyright"]["pythonVersion"] == "3.14"
    assert tools["ruff"]["target-version"] == "py314"
    assert tools["ruff"]["line-length"] == 100
    settings = json.loads((ROOT / ".vscode/settings.json").read_text())
    assert "python.analysis.typeCheckingMode" not in settings
    assert (ROOT / ".python-version").read_text().strip() == tools["pyright"]["pythonVersion"]
    assert settings["python.defaultInterpreterPath"] == "${workspaceFolder}/.venv/bin/python"


def test_locked_tools() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    names = {package["name"] for package in lock["package"]}

    for name in ("pytest", "ruff", "mypy", "pyright"):
        assert name in names
        version = next(package["version"] for package in lock["package"] if package["name"] == name)
        assert f"{name}=={version}" in config["dependency-groups"]["dev"]
    assert config["project"]["dependencies"] == ["cryptography==50.0.2", "websockets==17.1"]
    declared = [
        *config["project"]["dependencies"],
        *config["build-system"]["requires"],
        *config["dependency-groups"]["dev"],
    ]

    for dependency in declared:
        name, separator, version = dependency.partition("==")
        assert name
        assert separator == "=="
        assert version
        assert not any(char in version for char in "*<>=,~")


def test_ci_configuration() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text()
    assert "on:\n  workflow_dispatch:\n" in workflow
    assert "pull_request:" not in workflow
    assert "publish" not in workflow
    assert "contents: read" in workflow
    assert "path: .pysx-logs/\n          include-hidden-files: true" in workflow
    steps = [match.group(1).strip() for match in RUN_STEP.finditer(workflow)]
    assert steps, "parsed no run: steps; the workflow or this parser changed"
    assert steps == [" ".join(command) for command in (*BOOTSTRAP, ENTRYPOINT)], (
        "quality.yml and scripts/verify.py disagree; declare the gate in the manifest "
        "instead of adding a workflow step"
    )


def test_verification_manifest_covers_the_required_checks() -> None:
    manifest = {" ".join(gate.command) for gate in GATES}

    for command in (
        "python -m pysx.tooling.quality ruff",
        "python -m pysx.tooling.quality mypy",
        "python -m pysx.tooling.quality pyright",
        "pytest -q",
    ):
        assert f"uv run --project . {command}" in manifest
    assert "uv lock --check" in manifest
    assert {gate.tier for gate in GATES} == set(TIERS)
    assert len({gate.name for gate in GATES}) == len(GATES)
    assert SELECTORS["gates"] == tuple(tier for tier in TIERS if tier != "setup")
    assert all(gate.command for gate in GATES)


def test_verification_manifest_entrypoint_lists_its_gates() -> None:
    listed = subprocess.run(
        [*ENTRYPOINT[:-1], "all", "--list"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert len(listed) == len(GATES)
    assert all(gate.name in "\n".join(listed) for gate in GATES)


def test_diagnostic_mode_cannot_replace_the_gates_selector(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PYSX_CI_PROBE", "1")
    monkeypatch.setattr(sys, "argv", ["verify", "all", "--list"])
    assert main() == 0
    probe = capsys.readouterr().out
    assert "DIAGNOSTIC PROBE ONLY" in probe
    assert all(gate.name in probe for gate in PROBE_GATES)
    monkeypatch.setattr(sys, "argv", ["verify", "gates", "--list"])
    assert main() == 0
    required = capsys.readouterr().out
    assert "DIAGNOSTIC" not in required
    assert all(gate.name in required for gate in GATES if gate.tier != "setup")


def test_focused_diagnostic_mode_keeps_its_inputs_and_preserves_required_gates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PYSX_CI_PROBE", "1")
    monkeypatch.setenv("PYSX_CI_PROBE_GATE", "worker-ownership")
    monkeypatch.setattr(sys, "argv", ["verify", "all", "--list"])
    assert main() == 0
    focused = capsys.readouterr().out
    assert "worker-ownership" in focused
    assert all(gate.name not in focused for gate in GATES if gate.tier == "setup")
    assert "editor-diagnostics" not in focused
    assert "lsp-projection" not in focused
    monkeypatch.setenv("PYSX_CI_PROBE_GATE", "editor-diagnostics")
    assert main() == 0
    editor = capsys.readouterr().out
    assert "npm-editor" in editor
    assert "vscode-host" in editor
    assert "playwright" not in editor
    assert "worker-ownership" not in editor
    monkeypatch.setenv("PYSX_CI_PROBE_GATE", "browser-watch-restart")
    assert main() == 0
    browser = capsys.readouterr().out
    assert "npm-tests" in browser
    assert "playwright" in browser
    assert "npm-editor" not in browser
    assert "vscode-host" not in browser
    monkeypatch.setenv("PYSX_CI_PROBE_GATE", "browser-inspector")
    assert main() == 0
    inspector = capsys.readouterr().out
    assert "--suite inspector_tracing" in inspector
    assert "npm-tests" in inspector
    assert "vscode-host" not in inspector
    monkeypatch.setenv("PYSX_CI_PROBE_GATE", "editor-client")
    assert main() == 0
    client = capsys.readouterr().out
    assert "--suite client_verify" in client
    assert "vscode-host" in client
    assert "playwright" not in client
    monkeypatch.setenv("PYSX_CI_PROBE_GATE", "storage-benchmark")
    assert main() == 0
    storage = capsys.readouterr().out
    assert "tests/storage_benchmark.py" in storage
    assert "npm-" not in storage
    monkeypatch.setattr(sys, "argv", ["verify", "gates", "--list"])
    assert main() == 0
    required = capsys.readouterr().out
    assert all(gate.name in required for gate in GATES if gate.tier != "setup")


def test_maintained_scope() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]

    for name in ("pysx", "examples", "tests", "editor", "run_example.py"):
        assert name not in config["pyright"]["exclude"]
        assert name not in config["ruff"]["extend-exclude"]
    assert config["mypy"]["files"] == ["."]
    assert config["pyright"]["include"] == ["."]
