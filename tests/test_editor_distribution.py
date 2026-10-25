"""Bundled client packaging and selected-environment developer tooling."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, cast

import pytest

from pysx.tooling.tools import RUFF_REQUIREMENT, ToolingError, resolve_ruff

ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "editor"


@pytest.mark.parametrize("consumer", ["cli", "worker"])
def test_lint_qualification_rejects_selected_environment_decoy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str
) -> None:
    from pysx.tooling.editor.model import workspace_model
    from pysx.tooling.quality import main

    environment = tmp_path / "bin"
    environment.mkdir()
    fake = environment / "ruff"
    fake.write_text("#!/bin/sh\necho 'ruff 0.1.0'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    root = tmp_path / "project"
    root.mkdir()
    (root / "view.py").write_text("value = undefined_name\n")
    monkeypatch.setattr(sys, "executable", str(environment / "python"))
    monkeypatch.setattr(sys, "argv", ["quality", "ruff", str(root)])

    def run() -> object:
        if consumer == "cli":
            return main()

        return workspace_model(
            {"root": str(root), "revision": "_pysx_revision_lint", "buffers": {}}
        )

    with pytest.raises(ToolingError, match=f"requires {RUFF_REQUIREMENT}"):
        run()


@pytest.mark.parametrize("consumer", ["cli", "worker"])
def test_lint_qualification_ignores_path_decoy_and_preserves_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    consumer: str,
) -> None:
    from pysx.tooling.editor.model import workspace_model
    from pysx.tooling.quality import main

    decoy = tmp_path / "ruff"
    decoy.write_text("#!/bin/sh\nexit 99\n")
    decoy.chmod(decoy.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    root = tmp_path / "project"
    root.mkdir()
    (root / "view.py").write_text("value = undefined_name\n")

    if consumer == "cli":
        monkeypatch.setattr(sys, "argv", ["quality", "ruff", str(root)])
        assert main() == 1
        assert "F821 Undefined name `undefined_name`" in capsys.readouterr().out
    else:
        models = workspace_model(
            {"root": str(root), "revision": "_pysx_revision_lint", "buffers": {}}
        )
        assert "F821" in str(models)
        assert "undefined_name" in str(models)


@pytest.fixture(scope="module")
def vsix() -> zipfile.ZipFile:
    built = subprocess.run(
        [sys.executable, str(EDITOR / "build_vsix.py")],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )

    return zipfile.ZipFile(built.stdout.strip())


def manifest() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads((EDITOR / "package.json").read_text()))


def lockfile() -> dict[str, Any]:
    data = cast("dict[str, Any]", json.loads((EDITOR / "package-lock.json").read_text()))

    return cast("dict[str, Any]", data["packages"])


def test_client_verify_every_declared_dependency_is_exactly_pinned() -> None:
    package = manifest()

    for section in ("dependencies", "devDependencies"):
        for name, spec in cast("dict[str, str]", package.get(section, {})).items():
            assert spec[0].isdigit(), f"{name} is declared as a range: {spec}"


def test_client_verify_vsix_ships_the_runtime_closure_only(vsix: zipfile.ZipFile) -> None:
    shipped = {
        name.split("/")[2] for name in vsix.namelist() if name.startswith("extension/node_modules/")
    }
    assert "vscode-languageclient" in shipped
    assert "@vscode/test-electron" not in shipped
    assert not any("test-electron" in name for name in vsix.namelist())
    locked = lockfile()

    for package in shipped:
        assert f"node_modules/{package}" in locked, f"{package} is not in the lockfile"


def test_client_verify_shipped_versions_match_the_lockfile(vsix: zipfile.ZipFile) -> None:
    locked = lockfile()

    for name in vsix.namelist():
        parts = name.split("/")

        if len(parts) != 4 or parts[1] != "node_modules" or parts[3] != "package.json":
            continue
        found = json.loads(vsix.read(name))
        assert found["version"] == locked[f"node_modules/{parts[2]}"]["version"]


def test_client_verify_vsix_carries_no_checkout_path(vsix: zipfile.ZipFile) -> None:
    for name in vsix.namelist():
        if not name.endswith((".js", ".json")):
            continue
        body = vsix.read(name).decode("utf-8", errors="replace")
        assert str(ROOT) not in body, f"{name} embeds the checkout path"


def test_client_verify_both_clients_ship_and_default_to_the_worker(
    vsix: zipfile.ZipFile,
) -> None:
    names = set(vsix.namelist())
    assert {"extension/authoring.js", "extension/client.js"} <= names
    dispatcher = vsix.read("extension/extension.js").decode()
    assert "useLanguageServer" in dispatcher
    assert "./authoring.js" in dispatcher
    assert "./client.js" in dispatcher
    shipped = json.loads(vsix.read("extension/package.json"))
    setting = shipped["contributes"]["configuration"]["properties"]["pysx.useLanguageServer"]
    assert setting["default"] is False


def test_interpreter_routing_vsix_ships_the_workspace_router(vsix: zipfile.ZipFile) -> None:
    """Per-folder routing must be part of the installed artifact, not the checkout."""
    names = set(vsix.namelist())
    assert "extension/workspaces.js" in names
    router = vsix.read("extension/workspaces.js").decode()
    assert "onDidChangeWorkspaceFolders" in router
    assert "onDidChangeActiveEnvironmentPath" in router
    client = vsix.read("extension/client.js").decode()
    assert "./workspaces.js" in client


def test_client_verify_ruff_resolves_from_the_selected_environment() -> None:
    tool = resolve_ruff()
    assert tool.version == RUFF_REQUIREMENT
    assert tool.path.is_file()
    assert tool.path.parent == Path(sys.executable).parent


def test_client_verify_ruff_ignores_a_conflicting_path_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decoy = tmp_path / "ruff"
    decoy.write_text("#!/bin/sh\necho 'ruff 0.1.0'\n")
    decoy.chmod(decoy.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    assert shutil.which("ruff") == str(decoy)
    assert resolve_ruff().version == RUFF_REQUIREMENT


def test_client_verify_ruff_reports_a_missing_installation(tmp_path: Path) -> None:
    empty = tmp_path / "bin" / "python"
    empty.parent.mkdir()
    empty.touch()

    with pytest.raises(ToolingError) as error:
        resolve_ruff(empty)
    assert "pysx[dev]" in str(error.value)
    assert "PATH" in str(error.value)


def test_client_verify_ruff_rejects_an_unsupported_version(tmp_path: Path) -> None:
    fake = tmp_path / "ruff"
    fake.write_text("#!/bin/sh\necho 'ruff 0.1.0'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "python").touch()

    with pytest.raises(ToolingError) as error:
        resolve_ruff(tmp_path / "python")
    assert "0.1.0" in str(error.value)
    assert RUFF_REQUIREMENT in str(error.value)


def test_client_verify_cli_and_editor_resolve_the_same_ruff() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pysx.tooling.tools"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    reported = json.loads(result.stdout)
    tool = resolve_ruff()
    assert reported == {"name": "ruff", "path": str(tool.path), "version": tool.version}


def test_client_verify_cli_reports_a_failure_as_an_error(tmp_path: Path) -> None:
    (tmp_path / "python").touch()
    result = subprocess.run(
        [sys.executable, "-m", "pysx.tooling.tools", str(tmp_path / "python")],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 1
    assert "pysx[dev]" in json.loads(result.stdout)["error"]


def test_client_verify_dev_extra_pins_the_required_ruff() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert f'dev = ["ruff=={RUFF_REQUIREMENT}"]' in pyproject
    assert '"cryptography==50.0.2"' in pyproject
    assert '"websockets==17.1"' in pyproject


def test_editor_surface_installed_extension_declares_every_shipped_feature(
    vsix: zipfile.ZipFile,
) -> None:
    """What the VSIX advertises must match what the client and server provide."""
    shipped = json.loads(vsix.read("extension/package.json"))
    commands = {item["command"] for item in shipped["contributes"]["commands"]}
    assert {"pysx.restartServer", "pysx.configureTooling", "pysx.rename"} <= commands
    properties = shipped["contributes"]["configuration"]["properties"]
    assert {"pysx.pythonPath", "pysx.useLanguageServer"} <= set(properties)
    assert shipped["activationEvents"] == ["onLanguage:python"]
    assert shipped["main"] == "./extension.js"


def test_editor_surface_installed_extension_runs_outside_the_checkout(
    vsix: zipfile.ZipFile, tmp_path: Path
) -> None:
    """Extraction anywhere must yield a self-contained, runnable extension."""
    vsix.extractall(tmp_path)
    extension = tmp_path / "extension"
    assert (extension / "client.js").is_file()
    assert (extension / "node_modules" / "vscode-languageclient" / "package.json").is_file()
    entry = json.loads(
        (extension / "node_modules" / "vscode-languageclient" / "package.json").read_text()
    )
    main = entry.get("main") or "lib/node/main.js"
    assert (extension / "node_modules" / "vscode-languageclient" / main).exists() or (
        extension / "node_modules" / "vscode-languageclient" / "lib" / "node" / "main.js"
    ).exists()

    for name in (extension / "node_modules").iterdir():
        assert "test-electron" not in name.name


def test_resident_commands_vsix_ships_every_advertised_command(vsix: zipfile.ZipFile) -> None:
    """Advertised commands, their implementation and the dispatcher guard ship together."""
    shipped = json.loads(vsix.read("extension/package.json"))
    advertised = {item["command"] for item in shipped["contributes"]["commands"]}
    assert {
        "pysx.rename",
        "pysx.refresh",
        "pysx.configureTooling",
        "pysx.restartServer",
        "pysx.restoreTooling",
    } == advertised
    assert "extension/commands.js" in set(vsix.namelist())
    commands = vsix.read("extension/commands.js").decode()

    for command in advertised - {"pysx.restartServer"}:
        assert f'"{command}"' in commands, f"{command} has no resident implementation"
    assert '"pysx.restartServer"' in vsix.read("extension/client.js").decode()
    dispatcher = vsix.read("extension/extension.js").decode()
    assert "contributes.commands" in dispatcher or "contributes" in dispatcher
