"""Bundled client packaging and selected-environment developer tooling."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, cast
from xml.etree import ElementTree

import pytest

from pysx.tooling.tools import RUFF_REQUIREMENT, ToolingError, resolve_ruff

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "editor"


def test_editor_worker_matches_python_resolved_paths() -> None:
    # Windows TEMP can use an 8.3 directory name. Preserve that spelling until
    # both runtimes resolve it, instead of pre-normalizing it with tmp_path.
    with TemporaryDirectory(prefix="pysx-path-identity-") as temporary:
        filename = Path(temporary) / "view.py"
        filename.write_text("value = 1\n", encoding="utf-8")
        script = """
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const local = (name) => path.join(path.dirname(process.argv[1]), name);
// Shared modules are loaded through real `require`, so the stub lives at module level.
const Module = require('node:module');
const realRequire = Module.prototype.require;
const vscodeStub = {
  ConfigurationTarget: { WorkspaceFolder: 3 },
  commands: { registerCommand: () => ({ dispose() {} }) },
  window: { showWarningMessage() {}, showInformationMessage() {} },
  workspace: { getConfiguration: () => ({ get: () => undefined, update: async () => {} }) },
};
Module.prototype.require = function (name) {
  if (name === 'vscode') return vscodeStub;
  return realRequire.apply(this, arguments);
};
const result = {};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8') +
  '\\nexports.resolved = canonical(process.argv[2]);', {
  exports: result,
  process,
  require: (name) => name === 'vscode' ? vscodeStub
    : require(name.startsWith('.') ? local(name) : name),
});
console.log(JSON.stringify({worker: result.resolved,
  legacy: fs.realpathSync(process.argv[2]), native: fs.realpathSync.native(process.argv[2])}));
"""
        result = subprocess.run(
            ["node", "-e", script, str(EDITOR / "authoring.js"), str(filename)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=10,
        )
        identities = json.loads(result.stdout)
        assert identities["worker"] == str(filename.resolve()), identities


class RuffStub:
    """Keep discovery/stat real; simulate execution only for registered fixtures."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tools: dict[Path, tuple[str, int, Path | None]] = {}
        self.real_run = cast("Callable[..., subprocess.CompletedProcess[str]]", subprocess.run)
        monkeypatch.setattr(subprocess, "run", self.run)

    def __call__(
        self, path: Path, version: str = "0.1.0", *, code: int = 0, probes: Path | None = None
    ) -> Path:
        target = path.with_suffix(".exe") if sys.platform == "win32" else path
        target.write_text(version, encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IEXEC)
        self.tools[target] = (version, code, probes)

        return target

    def run(self, args: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        fixture = self.tools.get(Path(args[0]))

        if fixture is None:
            return self.real_run(args, **kwargs)
        version, code, probes = fixture

        if probes is not None:
            with probes.open("a", encoding="utf-8") as stream:
                stream.write("probe\n")
        result = subprocess.CompletedProcess(args, code, f"ruff {version}\n", "")

        if kwargs.get("check"):
            result.check_returncode()

        return result


@pytest.fixture
def ruff_stub(monkeypatch: pytest.MonkeyPatch) -> RuffStub:
    return RuffStub(monkeypatch)


@pytest.mark.parametrize("consumer", ["cli", "worker"])
def test_lint_qualification_rejects_selected_environment_decoy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str, ruff_stub: RuffStub
) -> None:
    from pysx.tooling.editor.model import workspace_model
    from pysx.tooling.quality import main

    environment = tmp_path / "bin"
    environment.mkdir()
    ruff_stub(environment / "ruff")
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
    ruff_stub: RuffStub,
) -> None:
    from pysx.tooling.editor.model import workspace_model
    from pysx.tooling.quality import main

    ruff_stub(tmp_path / "ruff", code=99)
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


def test_lint_qualification_follows_the_requested_interpreter(
    tmp_path: Path, ruff_stub: RuffStub
) -> None:
    """Readiness and the type backend use the client's interpreter; lint must agree."""
    from pysx.tooling.editor.model import workspace_model
    from pysx.tooling.lsp.backend import Stamp
    from pysx.tooling.lsp.workspace import WorkspaceIndex

    environment = tmp_path / "bin"
    environment.mkdir()
    decoy = environment / "python"
    decoy.touch()
    ruff_stub(environment / "ruff")
    root = tmp_path / "project"
    root.mkdir()
    (root / "view.py").write_text("value = undefined_name\n")
    models = workspace_model({"root": str(root), "revision": "_pysx_revision_lint", "buffers": {}})
    assert "F821" in str(models)

    with pytest.raises(ToolingError, match=f"requires {RUFF_REQUIREMENT}"):
        workspace_model(
            {
                "root": str(root),
                "revision": "_pysx_revision_lint",
                "buffers": {},
                "interpreter": str(decoy),
            }
        )

    index = WorkspaceIndex(root)
    snapshot = index.build({}, Stamp(1, 0), interpreter=str(decoy))
    assert f"requires {RUFF_REQUIREMENT}" in str(index.lint_error)
    assert snapshot.file(str(root / "view.py")) is not None


def test_lint_qualification_probes_a_binary_once_until_it_changes(
    tmp_path: Path, ruff_stub: RuffStub
) -> None:
    """Linting resolves the tool per source, so a probe per call would dominate the gate."""
    probes = tmp_path / "probes"
    environment = tmp_path / "bin"
    environment.mkdir()
    interpreter = environment / "python"
    interpreter.touch()
    ruff = environment / "ruff"

    def install(version: str) -> None:
        ruff_stub(ruff, version, probes=probes)

    install(RUFF_REQUIREMENT)
    assert resolve_ruff(interpreter).version == RUFF_REQUIREMENT
    assert resolve_ruff(interpreter).version == RUFF_REQUIREMENT
    assert probes.read_text().count("probe") == 1
    install("0.1.0")

    with pytest.raises(ToolingError, match=f"requires {RUFF_REQUIREMENT}"):
        resolve_ruff(interpreter)
    assert probes.read_text().count("probe") == 2


def test_lint_qualification_remembers_no_unusable_environment(
    tmp_path: Path, ruff_stub: RuffStub
) -> None:
    """A later install must take effect without restarting the resident server."""
    environment = tmp_path / "bin"
    environment.mkdir()
    interpreter = environment / "python"
    interpreter.touch()

    with pytest.raises(ToolingError, match="pysx-framework\\[dev\\]"):
        resolve_ruff(interpreter)
    ruff_stub(environment / "ruff", RUFF_REQUIREMENT)
    assert resolve_ruff(interpreter).version == RUFF_REQUIREMENT


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


def test_stable_api_editor_license(vsix: zipfile.ZipFile) -> None:
    package = manifest()
    assert package["license"] == "MIT"
    assert package["author"] == "JMS Apps <jmsapps@outlook.com>"
    assert lockfile()[""]["version"] == package["version"]
    assert vsix.read("extension/LICENSE.txt") == (ROOT / "LICENSE").read_bytes()


def test_stable_api_vsix_manifest_satisfies_the_gallery(vsix: zipfile.ZipFile) -> None:
    """`code --install-extension` ignores the manifest; the Marketplace does not."""
    package = manifest()
    namespace = {"v": "http://schemas.microsoft.com/developer/vsx-schema/2011"}
    root = ElementTree.fromstring(vsix.read("extension.vsixmanifest").decode())
    identity = root.find("./v:Metadata/v:Identity", namespace)
    assert identity is not None
    assert identity.get("Id") == package["name"] == "pysx"
    assert identity.get("Publisher") == package["publisher"] == "jmsapps"
    assert identity.get("Version") == package["version"]
    properties = {
        node.get("Id"): node.get("Value")
        for node in root.findall("./v:Metadata/v:Properties/v:Property", namespace)
    }
    assert properties["Microsoft.VisualStudio.Code.Engine"] == package["engines"]["vscode"]
    assets = {
        node.get("Type"): node.get("Path") for node in root.findall("./v:Assets/v:Asset", namespace)
    }
    names = set(vsix.namelist())

    for kind in (
        "Microsoft.VisualStudio.Code.Manifest",
        "Microsoft.VisualStudio.Services.Content.Details",
        "Microsoft.VisualStudio.Services.Content.License",
        "Microsoft.VisualStudio.Services.Icons.Default",
    ):
        assert assets.get(kind) in names, f"{kind} points outside the archive: {assets.get(kind)}"
    typed = {
        node.get("Extension")
        for node in ElementTree.fromstring(vsix.read("[Content_Types].xml").decode()).findall(
            "./{http://schemas.openxmlformats.org/package/2006/content-types}Default"
        )
    }

    for name in names:
        suffix = PurePosixPath(name).suffix

        # The type map is the one part OPC does not type through itself.
        if suffix and name != "[Content_Types].xml":
            assert suffix in typed, f"{name} has no content type declared"


def test_stable_api_vsix_excludes_development_only_inputs(vsix: zipfile.ZipFile) -> None:
    """The sideload tree carries the harness and the builder; the package must not."""
    basenames = {PurePosixPath(name).name for name in vsix.namelist()}

    for unwanted in ("build_vsix.py", "pysx-root.json", "package-lock.json"):
        assert unwanted not in basenames, f"{unwanted} is shipped"
    assert not any(name.endswith(".vsix") for name in basenames)
    assert not any(name.startswith("extension/test/") for name in vsix.namelist())


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ruff_stub: RuffStub
) -> None:
    decoy = ruff_stub(tmp_path / "ruff")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    found = shutil.which("ruff")
    assert found is not None
    assert Path(found) == decoy
    assert resolve_ruff().version == RUFF_REQUIREMENT


def test_client_verify_ruff_reports_a_missing_installation(tmp_path: Path) -> None:
    empty = tmp_path / "bin" / "python"
    empty.parent.mkdir()
    empty.touch()

    with pytest.raises(ToolingError) as error:
        resolve_ruff(empty)
    assert "pysx-framework[dev]" in str(error.value)
    assert "PATH" in str(error.value)


def test_client_verify_ruff_rejects_an_unsupported_version(
    tmp_path: Path, ruff_stub: RuffStub
) -> None:
    ruff_stub(tmp_path / "ruff")
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
    assert "pysx-framework[dev]" in json.loads(result.stdout)["error"]


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
    shipped = set(vsix.namelist())

    for name in ("commands.js", "setup.js", "tooling.js", "projects.js"):
        assert f"extension/{name}" in shipped, f"{name} is missing from the artifact"
    # Setup and restore are implemented once, in the shared controller, and the
    # mode-specific modules supply only their adapters.
    implementations = "".join(
        vsix.read(f"extension/{name}").decode() for name in ("commands.js", "setup.js")
    )

    for command in advertised - {"pysx.restartServer"}:
        assert f'"{command}"' in implementations, f"{command} has no resident implementation"
    worker = vsix.read("extension/authoring.js").decode()
    assert '"pysx.configureTooling"' not in worker, "the worker registers setup separately"
    assert '"pysx.restartServer"' in vsix.read("extension/client.js").decode()
    dispatcher = vsix.read("extension/extension.js").decode()
    assert "contributes.commands" in dispatcher or "contributes" in dispatcher


NAVIGATION_HARNESS = """
const assert = require("node:assert/strict");
const timing = require(process.argv[1]);

// A controlled clock makes the measured interval exact rather than approximately right.
function clockOver(stamps) {
  const values = [...stamps];
  let last = 0;
  return () => { last = values.length ? values.shift() : last; return last; };
}
const target = { uri: { fsPath: "/app/controls.py" }, range: { start: { line: 2 } } };
const expect = (found) => found.uri.fsPath === "/app/controls.py" ? null
  : `expected /app/controls.py, found ${found.uri.fsPath}`;

(async () => {
  // 1. The interval is provider invocation until the result resolves.
  const delayed = await timing.sample({
    clock: clockOver([0, 120]), request: async () => [target], expect, label: "delayed",
  });
  assert.equal(delayed.ok, true, JSON.stringify(delayed));
  assert.equal(delayed.ms, 120, "the harness observes the controlled delay exactly");

  // 2. A wrong destination is a failure, never a timing sample.
  const wrong = await timing.sample({
    clock: clockOver([0, 5]),
    request: async () => [{ uri: { fsPath: "/outer/controls.py" }, range: { start: { line: 2 } } }],
    expect, label: "wrong",
  });
  assert.equal(wrong.ok, false);
  assert.equal(wrong.reason, "wrong-target");

  // 3. An empty result is recorded as a failure with its own reason.
  const empty = await timing.sample({
    clock: clockOver([0, 5]), request: async () => [], expect, label: "empty",
  });
  assert.equal(empty.ok, false);
  assert.equal(empty.reason, "empty");

  // 4. A timeout is recorded rather than hanging or being dropped.
  const timedOut = await timing.sample({
    clock: clockOver([0, 30]), request: () => new Promise(() => {}), expect,
    label: "timeout", timeout: 25,
  });
  assert.equal(timedOut.ok, false);
  assert.equal(timedOut.reason, "timeout");

  // 5. Statistics describe only successful samples, and failures stay counted.
  let call = 0;
  const mixed = await timing.scenario({
    label: "ready", repetitions: 4, warmup: 1,
    clock: clockOver([0, 10, 0, 10, 0, 20, 0, 30, 0, 40]),
    request: async () => (++call === 3 ? [] : [target]),
    expect,
  });
  assert.equal(mixed.samples.length, 4, "warm-up samples are excluded");
  assert.equal(mixed.ok, 3);
  assert.equal(mixed.failures, 1);
  assert.equal(mixed.median_ms, 30);
  assert.equal(mixed.min_ms, 10);
  assert.equal(mixed.max_ms, 40);

  assert.equal(timing.median([]), null, "no samples yield no median");
  console.log("NAVIGATION HARNESS OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""


def test_navigation_measurement_harness_boundaries_and_failures() -> None:
    """The timing harness must observe real delays and refuse to time a wrong answer."""
    module = EDITOR / "test" / "navigation_timing.cjs"
    assert module.is_file(), "the navigation timing module is registered"
    result = subprocess.run(
        ["node", "-e", NAVIGATION_HARNESS, str(module)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NAVIGATION HARNESS OK" in result.stdout


def test_navigation_measurement_suite_is_registered() -> None:
    """An unregistered suite would never run inside the unfiltered editor gate."""
    runner = (EDITOR / "test" / "runner.mjs").read_text(encoding="utf-8")
    suite = (EDITOR / "test" / "suite.cjs").read_text(encoding="utf-8")
    assert '"navigation_latency"' in runner, "the suite is selectable"
    assert "navigation_latency_suite.cjs" in suite, "the host dispatches the suite"
    assert (EDITOR / "test" / "navigation_latency_suite.cjs").is_file()


MANAGED_PROBE = """
const tooling = require(process.argv[1]);
const { execFileSync } = require("node:child_process");
const options = JSON.parse(process.argv[2]);
(async () => {
  const result = await tooling.provision(options);
  const version = (tool) =>
    execFileSync(result.tools[tool], ["--version"], { encoding: "utf8", timeout: 180000 }).trim();
  console.log(JSON.stringify({
    root: result.root, installer: result.installer ?? null, provisioned: result.provisioned,
    tools: result.tools, site: result.site,
    versions: { ruff: version("ruff"), pyright: version("pyright") },
  }));
})().catch((error) => { console.error(error.message); process.exitCode = 1; });
"""


def managed_closure(storage: Path, interpreter: Path, **options: object) -> dict[str, Any]:
    payload = {"storage": str(storage), "interpreter": str(interpreter), **options}
    result = subprocess.run(
        ["node", "-e", MANAGED_PROBE, str(EDITOR / "tooling.js"), json.dumps(payload)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    return cast("dict[str, Any]", json.loads(result.stdout))


@pytest.fixture(scope="module")
def application_without_extras(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real installed application carrying PYSX but no editor-only tooling extras."""
    from prototypes.distribution import dependency_cache, installed

    workspace = tmp_path_factory.mktemp("managed-tooling")
    uv = shutil.which("uv")
    assert uv is not None, "uv builds the application wheel"
    cache = dependency_cache(workspace / "cache")
    artifacts = workspace / "artifacts"
    subprocess.run(
        [
            uv,
            "--offline",
            "--cache-dir",
            str(cache),
            "build",
            str(ROOT),
            "--wheel",
            "--out-dir",
            str(artifacts),
            "--python",
            sys.executable,
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=900,
        check=True,
    )

    return installed(workspace, next(artifacts.glob("*.whl")), cache, extra=False)


def installed_names(python: Path) -> set[str]:
    result = subprocess.run(
        [
            str(python),
            "-P",
            "-c",
            "import json;from importlib.metadata import distributions;"
            "print(json.dumps(sorted(d.metadata['Name'].lower() for d in distributions())))",
        ],
        # Outside the checkout and without the working directory on the path: otherwise
        # this repository's own sources answer for the installed application's.
        cwd=python.parent.parent,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )

    return set(cast("list[str]", json.loads(result.stdout)))


def test_managed_tooling_uses_application_pysx_and_private_tools(
    application_without_extras: Path, tmp_path: Path
) -> None:
    """Analysis must come from the application's PYSX while tools stay extension-owned."""
    python = application_without_extras
    before = installed_names(python)
    assert "pysx-framework" in before
    assert not {"ruff", "pyright", "pygls", "lsprotocol"} & before, (
        f"the application starts without editor extras: {sorted(before)}"
    )
    closure = managed_closure(tmp_path / "storage", python)
    assert closure["provisioned"] is True
    assert closure["versions"]["ruff"] == f"ruff {RUFF_REQUIREMENT}"
    assert "1.1.414" in closure["versions"]["pyright"]

    # The tools must not resolve inside the application environment.
    for tool in closure["tools"].values():
        assert Path(tool).is_relative_to(tmp_path), tool
        assert not Path(tool).is_relative_to(python.parent.parent), tool

    # The application's PYSX is the one that supplies analysis.
    origin = subprocess.run(
        [str(python), "-P", "-c", "import pysx, json;print(json.dumps(pysx.__file__))"],
        cwd=python.parent.parent,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert Path(json.loads(origin.stdout)).is_relative_to(python.parent.parent)

    # Provisioning never adds anything to the application environment.
    assert installed_names(python) == before


def test_managed_tooling_reuses_a_verified_closure(
    application_without_extras: Path, tmp_path: Path
) -> None:
    storage = tmp_path / "storage"
    first = managed_closure(storage, application_without_extras)
    second = managed_closure(storage, application_without_extras)
    assert first["provisioned"] is True
    assert second["provisioned"] is False, "a verified closure is reused rather than rebuilt"
    assert second["root"] == first["root"]


def test_managed_tooling_rejects_a_corrupt_closure(
    application_without_extras: Path, tmp_path: Path
) -> None:
    """A pruned or half-written cache must be reprovisioned, never trusted."""
    storage = tmp_path / "storage"
    first = managed_closure(storage, application_without_extras)
    Path(first["tools"]["ruff"]).unlink()
    repaired = managed_closure(storage, application_without_extras)
    assert repaired["provisioned"] is True
    assert repaired["versions"]["ruff"] == f"ruff {RUFF_REQUIREMENT}"


def test_managed_tooling_offline_without_a_cache_is_actionable(tmp_path: Path) -> None:
    payload = {
        "storage": str(tmp_path / "storage"),
        "interpreter": sys.executable,
        "offline": True,
    }
    result = subprocess.run(
        ["node", "-e", MANAGED_PROBE, str(EDITOR / "tooling.js"), json.dumps(payload)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode != 0
    assert "offline" in result.stderr
    assert "pysx.toolingPath" in result.stderr, "the failure names a way forward"


def test_managed_tooling_ignores_a_bare_installer_name(tmp_path: Path) -> None:
    """The GUI PATH is not the shell PATH, so only an absolute installer may be used."""
    closure = managed_closure(tmp_path / "storage", Path(sys.executable), uv="uv")
    assert closure["installer"] == "ensurepip"


CHILD_PROBE = """
const tooling = require(process.argv[1]);
const { execFileSync } = require("node:child_process");
const options = JSON.parse(process.argv[2]);
(async () => {
  const closure = await tooling.provision(options);
  const environment = tooling.childEnvironment(closure, { PATH: process.env.PATH });
  const script = [
    "import json, sys",
    "import pysx, ruff, pyright",
    "from importlib.metadata import distribution",
    "print(json.dumps({",
    "  'pysx': pysx.__file__,",
    "  'ruff': ruff.__file__,",
    "  'pyright_version': distribution('pyright').version,",
    "  'pth_executed': hasattr(sys, '_pysx_pth_marker'),",
    "  'conflict': __import__('conflictprobe').ORIGIN,",
    "}))",
  ].join("\\n");
  const stdout = execFileSync(options.interpreter, ["-c", script],
    { encoding: "utf8", timeout: 180000, env: environment, cwd: require("node:os").tmpdir() });
  console.log(JSON.stringify({
    child: JSON.parse(stdout), managed: environment.PYSX_MANAGED_TOOLING }));
})().catch((error) => { console.error(error.message); process.exitCode = 1; });
"""


def test_managed_tooling_child_prefers_the_closure_over_conflicting_packages(
    application_without_extras: Path, tmp_path: Path
) -> None:
    """Parent sys.path does not propagate, so each child names the closure explicitly."""
    python = application_without_extras
    site = subprocess.run(
        [str(python), "-P", "-c", "import site;print(site.getsitepackages()[0])"],
        cwd=python.parent.parent,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    ).stdout.strip()

    # A conflicting copy installed in the application environment, plus a .pth hook that
    # must never run when the closure is exposed through PYTHONPATH.
    (Path(site) / "conflictprobe").mkdir(parents=True, exist_ok=True)
    (Path(site) / "conflictprobe" / "__init__.py").write_text("ORIGIN = 'application'\n")
    storage = tmp_path / "storage"
    closure = managed_closure(storage, python)

    for directory in cast("list[str]", closure["site"]):
        probe = Path(directory) / "conflictprobe"
        probe.mkdir(parents=True, exist_ok=True)
        (probe / "__init__.py").write_text("ORIGIN = 'closure'\n")
        (Path(directory) / "pysx_probe.pth").write_text("import sys; sys._pysx_pth_marker = True\n")

    result = subprocess.run(
        [
            "node",
            "-e",
            CHILD_PROBE,
            str(EDITOR / "tooling.js"),
            json.dumps({"storage": str(storage), "interpreter": str(python)}),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = cast("dict[str, Any]", json.loads(result.stdout))
    child = cast("dict[str, Any]", observed["child"])

    assert child["conflict"] == "closure", "the pinned closure outranks an application copy"
    assert child["pth_executed"] is False, "dependency .pth hooks must never be executed"
    assert child["pyright_version"] == "1.1.414"
    assert Path(cast("str", child["ruff"])).is_relative_to(storage), child["ruff"]
    # PYSX still comes from the application, which is what supplies analysis.
    origin = Path(cast("str", child["pysx"]))
    assert origin.is_relative_to(python.parent.parent), origin
    assert observed["managed"] == closure["root"]


def test_managed_tooling_ruff_resolution_prefers_an_explicit_location(tmp_path: Path) -> None:
    """The editor supplies a resolved location; the CLI keeps its co-location behaviour."""
    from pysx.tooling.tools import RUFF_REQUIREMENT, ToolingError, resolve_ruff

    closure = managed_closure(tmp_path / "storage", Path(sys.executable))
    managed = Path(cast("str", closure["root"]))
    empty = tmp_path / "empty"
    (empty / "bin").mkdir(parents=True)
    interpreter = empty / "bin" / "python"
    interpreter.write_text("")

    tool = resolve_ruff(interpreter, location=managed)
    assert tool.version == RUFF_REQUIREMENT
    assert Path(tool.path).is_relative_to(managed)

    with pytest.raises(ToolingError, match="is not installed"):
        resolve_ruff(interpreter)
