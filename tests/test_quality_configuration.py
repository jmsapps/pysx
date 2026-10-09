"""Check the repository's strict quality configuration and its verification manifest."""

import json
import re
import subprocess
import tomllib
from pathlib import Path

from scripts.verify import BOOTSTRAP, ENTRYPOINT, GATES, SELECTORS, TIERS

ROOT = Path(__file__).resolve().parents[1]
RUN_STEP = re.compile(r"^\s*- run: (.+)$", re.MULTILINE)


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
    assert config["project"]["dependencies"] == ["websockets==17.1"]
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
    assert "on:\n  push:\n" in workflow
    assert "pull_request:" not in workflow
    assert "publish" not in workflow
    assert "contents: read" in workflow
    steps = [match.group(1).strip() for match in RUN_STEP.finditer(workflow)]
    assert steps, "parsed no run: steps; the workflow or this parser changed"
    assert steps == [" ".join(command) for command in (*BOOTSTRAP, ENTRYPOINT)], (
        "quality.yml and scripts/verify.py disagree; declare the gate in the manifest "
        "instead of adding a workflow step"
    )


def test_verification_manifest_covers_the_required_checks() -> None:
    manifest = {" ".join(gate.command) for gate in GATES}

    for command in (
        "python -m pysx.quality ruff",
        "python -m pysx.quality mypy",
        "python -m pysx.quality pyright",
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


def test_maintained_scope() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]

    for name in ("pysx", "examples", "tests", "editor", "run_example.py"):
        assert name not in config["pyright"]["exclude"]
        assert name not in config["ruff"]["extend-exclude"]
    assert config["mypy"]["files"] == ["."]
    assert config["pyright"]["include"] == ["."]
