"""Check the repository's strict quality configuration."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pysx_2_st_1_strict_scope() -> None:
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


def test_pysx_2_st_1_locked_tools() -> None:
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


def test_pysx_2_st_4_ci_configuration() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text()
    assert "on:\n  push:\n  pull_request:\n" in workflow
    assert "uv sync --locked --python 3.14.4" in workflow
    assert "uv python install 3.14.4" in workflow
    for command in ("ruff check .", "mypy", "pyright", "pytest -q"):
        assert f"uv run --project . {command}" in workflow
    assert "publish" not in workflow
    assert "contents: read" in workflow


def test_pysx_2_st_4_maintained_scope() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]
    for name in ("pysx", "examples", "tests", "editor", "run_example.py"):
        assert name not in config["pyright"]["exclude"]
        assert name not in config["ruff"]["extend-exclude"]
    assert config["mypy"]["files"] == ["."]
    assert config["pyright"]["include"] == ["."]
