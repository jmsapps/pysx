"""Public distribution metadata and packaging declarations stay consistent."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_stable_api_owner_metadata() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["authors"] == [{"name": "JMS Apps", "email": "jmsapps@outlook.com"}]
    assert "Copyright (c) 2026 JMS Apps" in (ROOT / "LICENSE").read_text()


def test_stable_api_commands_and_pins() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["scripts"]["pysx-example"] == "pysx.tooling.examples:app"
    assert project["requires-python"] == ">=3.14"

    for dependencies in [project["dependencies"], *project["optional-dependencies"].values()]:
        assert all("==" in dependency for dependency in dependencies)
