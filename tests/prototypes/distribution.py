"""Scratch-only installed-distribution and portable launcher architecture proofs.

The copied candidate package is never shipped or imported into the live repository.
Dependencies come from a copied existing uv cache, exclusively offline.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from importlib.metadata import distribution
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]

LSP_SOURCE = '''"""Scratch stdio transport proof, not the production language server."""
import json
import sys

def main():
    if sys.argv[1:] != ["--stdio"]:
        raise SystemExit("use --stdio")
    while True:
        headers = {}
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return
            if line == b"\\r\\n":
                break
            key, value = line.decode("ascii").split(":", 1)
            headers[key.lower()] = value.strip()
        request = json.loads(sys.stdin.buffer.read(int(headers["content-length"])))
        method = request["method"]
        if method == "exit":
            return
        if method == "initialize":
            result = {"capabilities": {}, "serverInfo": {
                "name": "pysx-architecture-proof", "version": "0.1.0"}, "proof": {
                "transport": "stdio", "isolated": True, "shutdown": False,
                "executable": sys.executable, "sys_path": sys.path}}
        elif method == "shutdown":
            result = None
        else:
            raise SystemExit("unsupported proof method")
        payload = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
        sys.stdout.buffer.write(f"Content-Length: {len(payload)}\\r\\n\\r\\n".encode() + payload)
        sys.stdout.buffer.flush()

if __name__ == "__main__":
    main()
'''

CLI_SOURCE = '''"""Scratch installed standalone serve entrypoint."""
from pysx.server import main as serve

def main():
    serve()
'''

DEMO_SOURCE = """from pysx import pysx

def app():
    return pysx(t"div: installed artifact proof")
"""

EXTENSION_SOURCE = r"""const vscode = require("vscode");
const { spawn } = require("node:child_process");
const fs = require("node:fs");

function frame(message) {
  const payload = Buffer.from(JSON.stringify(message), "utf8");
  return Buffer.concat([Buffer.from(`Content-Length: ${payload.length}\r\n\r\n`), payload]);
}
async function launch(explicitPython) {
  let python = explicitPython ?? vscode.workspace.getConfiguration("pysxProof").get("python");
  if (!python) {
    const extension = vscode.extensions.getExtension("ms-python.python");
    const api = await extension?.activate();
    python = api?.environments?.getActiveEnvironmentPath()?.path;
  }
  if (!python || !fs.existsSync(python)) {
    throw new Error("select an installed Python 3.14+ with pysx");
  }
  const child = spawn(python, ["-I", "-m", "pysx.lsp", "--stdio"], {
    cwd: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
    stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, PYTHONPATH: "" },
  });
  let buffer = Buffer.alloc(0), stderr = "", result;
  return await new Promise((resolve, reject) => {
    const deadline = setTimeout(() => {
      child.kill(); reject(new Error("stdio deadline"));
    }, 10000);
    child.stderr.on("data", chunk => { stderr = (stderr + chunk).slice(-4096); });
    child.once("error", error => { clearTimeout(deadline); reject(error); });
    child.stdout.on("data", chunk => {
      buffer = Buffer.concat([buffer, chunk]);
      while (true) {
        const end = buffer.indexOf("\r\n\r\n");
        if (end < 0) return;
        const header = buffer.subarray(0, end).toString("ascii");
        const length = Number(/Content-Length: (\d+)/i.exec(header)?.[1]);
        if (!Number.isSafeInteger(length) || length < 0 || length > 65536) {
          child.kill(); clearTimeout(deadline); reject(new Error("invalid stdio frame")); return;
        }
        if (buffer.length < end + 4 + length) return;
        const message = JSON.parse(buffer.subarray(end + 4, end + 4 + length));
        buffer = buffer.subarray(end + 4 + length);
        if (message.id === 1) {
          result = message.result;
          child.stdin.write(frame({ jsonrpc: "2.0", id: 2, method: "shutdown" }));
        } else if (message.id === 2) {
          child.stdin.end(frame({ jsonrpc: "2.0", method: "exit" }));
        }
      }
    });
    child.once("close", code => {
      clearTimeout(deadline);
      if (code === 0 && result) {
        result.proof.shutdown = true;
        resolve(result);
      } else reject(new Error(`stdio exit ${code}: ${stderr}`));
    });
    child.stdin.write(frame({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }));
  });
}
function activate(context) {
  context.subscriptions.push(vscode.commands.registerCommand("pysxProof.launch", launch));
}
module.exports = { activate, deactivate() {}, launch };
"""


class ProofError(RuntimeError):
    pass


def clean_env() -> dict[str, str]:
    env = dict(os.environ)

    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT", "UV_WORKING_DIR"):
        env.pop(key, None)
    env["PYTHONNOUSERSITE"] = "1"

    return env


def command(
    args: Sequence[str],
    cwd: Path,
    *,
    expected: int = 0,
    input_text: str | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=clean_env(),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )

    if result.returncode != expected:
        raise ProofError(f"{args[0]} exited {result.returncode}: {result.stdout}{result.stderr}")

    return result


EXAMPLES_EXTRA = "typer==0.27.2"


def required_pins() -> list[str]:
    """The candidate's own declared installs: runtime plus the examples extra."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = cast("dict[str, object]", metadata["project"])

    return [*cast("list[str]", project["dependencies"]), EXAMPLES_EXTRA]


def dependency_cache(destination: Path) -> Path:
    """Isolated dependency cache, never mutating any source cache.

    Copying an existing cache is only a fast path; its internal layout varies by uv
    version and platform, and a clean machine has nothing to copy. Whenever the copy
    cannot satisfy the declared pins the cache is warmed from the index once, here.
    Every later step runs --offline, so an undeclared dependency still cannot be
    fetched silently.
    """
    names = (
        "websockets",
        "typer",
        "shellingham",
        "rich",
        "annotated-doc",
        "markdown-it-py",
        "mdurl",
        "pygments",
        "colorama",
    )
    candidates = [Path.home() / ".cache" / "uv"]
    configured = os.environ.get("UV_CACHE_DIR")

    if configured:
        candidates.insert(0, Path(configured))
    destination.mkdir(parents=True, exist_ok=True)

    for name in names:
        for source in candidates:
            package = source / "wheels-v6" / "pypi" / name

            if package.is_dir():
                target = destination / "wheels-v6" / "pypi" / name

                if not target.exists():
                    shutil.copytree(package, target, symlinks=True)

                    for link in target.iterdir():
                        if link.is_symlink():
                            original = link.resolve()
                            copied_archive = destination / "archive-v0" / original.name

                            if not copied_archive.exists():
                                copied_archive.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copytree(original, copied_archive)
                            link.unlink()
                            link.symlink_to(copied_archive, target_is_directory=True)
                index = source / "simple-v21" / "pypi" / f"{name}.rkyv"

                if index.is_file():
                    copied = destination / "simple-v21" / "pypi" / index.name
                    copied.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(index, copied)

                break

    return warmed(destination)


def warmed(destination: Path) -> Path:
    uv = shutil.which("uv")

    if uv is None:
        raise ProofError("uv is required")
    pins = required_pins()
    probe = destination.parent / "cache-probe"
    install = [
        uv,
        "--cache-dir",
        str(destination),
        "pip",
        "install",
        "--python",
        sys.executable,
        "--target",
        str(probe),
        *pins,
    ]

    try:
        command([install[0], "--offline", *install[1:]], destination.parent)
    except ProofError:
        try:
            command(install, destination.parent, timeout=300)
        except ProofError as error:
            raise ProofError(
                f"isolated cache cannot supply {pins}; no reusable cache and the index "
                f"was unreachable: {error}"
            ) from error
    finally:
        shutil.rmtree(probe, ignore_errors=True)

    return destination


def candidate(destination: Path) -> None:
    destination.mkdir(parents=True)

    for module in ("pysx", "examples"):
        shutil.copytree(
            ROOT / module,
            destination / module,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    (destination / "pysx" / "py.typed").write_text("")
    shutil.copy2(ROOT / "run_example.py", destination / "run_example.py")
    runner = (ROOT / "run_example.py").read_text()
    runner = runner.replace(
        '"run_example.py needs Typer, which ships in the dev dependency group.\\n"',
        '"example needs the optional pysx[examples] extra.\\n"',
    ).replace(
        '"run it through uv:  uv run --project . python run_example.py run <name>"',
        '"Install pysx[examples], or use uv run --project . example from a checkout."',
    )
    (destination / "pysx" / "_example_runner.py").write_text(runner)

    for name, source in (
        ("cli.py", CLI_SOURCE),
        ("lsp.py", LSP_SOURCE),
        ("_distribution_demo.py", DEMO_SOURCE),
    ):
        (destination / "pysx" / name).write_text(source)
    metadata = (ROOT / "pyproject.toml").read_text()
    metadata = metadata.replace(
        'example = "run_example:app"',
        '''example = "pysx._example_runner:app"
pysx = "pysx.cli:main"
pysx-lsp = "pysx.lsp:main"''',
    )
    metadata = metadata.replace(
        "[build-system]",
        f"""[project.optional-dependencies]
examples = ["{EXAMPLES_EXTRA}"]

[build-system]""",
    )
    metadata = metadata.replace(
        'module-root = ""',
        """module-root = ""
module-name = ["pysx", "examples"]
source-include = ["run_example.py"]""",
    )
    (destination / "pyproject.toml").write_text(metadata)


def build_artifacts(workspace: Path) -> tuple[Path, Path, Path, Path]:
    source, cache = workspace / "candidate", dependency_cache(workspace / "uv-cache")
    candidate(source)
    uv = shutil.which("uv")

    if uv is None:
        raise ProofError("uv is required")
    version = command([uv, "--version"], workspace).stdout.strip()

    if version != "uv 0.11.8 (Homebrew 2026-04-27 aarch64-apple-darwin)" and not version.startswith(
        "uv 0.11.8 "
    ):
        raise ProofError(f"pinned bundled backend proof requires uv 0.11.8, got {version}")
    arguments = [uv, "--offline", "--cache-dir", str(cache), "build", "--python", sys.executable]
    command([*arguments, str(source), "--wheel", "--sdist"], workspace)
    wheel = next((source / "dist").glob("*.whl"))
    sdist = next((source / "dist").glob("*.tar.gz"))
    reconstructed = workspace / "sdist-wheel"
    unpacked = workspace / "unpacked-sdist"
    unpacked.mkdir()
    with tarfile.open(sdist) as archive:
        archive.extractall(unpacked, filter="data")
    restored_source = next(path for path in unpacked.iterdir() if path.is_dir())
    # Building the extracted sdist directory uses the same bundled pinned backend.
    # uv's direct archive frontend requires separately cached uv_build even at the
    # same version; no network/bootstrap package is needed for this supported route.
    command(
        [*arguments, str(restored_source), "--wheel", "--out-dir", str(reconstructed)],
        workspace,
    )

    return wheel, sdist, next(reconstructed.glob("*.whl")), cache


def installed(workspace: Path, wheel: Path, cache: Path, *, extra: bool) -> Path:
    uv = shutil.which("uv")

    if uv is None:
        raise ProofError("uv is required")
    environment = workspace / ("examples-env" if extra else "core-env")
    command(
        [
            uv,
            "--offline",
            "--cache-dir",
            str(cache),
            "venv",
            "--python",
            sys.executable,
            str(environment),
        ],
        workspace,
    )
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    args = [
        uv,
        "--offline",
        "--cache-dir",
        str(cache),
        "pip",
        "install",
        "--python",
        str(python),
        "--link-mode",
        "copy",
    ]

    if extra:
        constraints = workspace / "constraints.txt"
        names = (
            "typer",
            "shellingham",
            "rich",
            "annotated-doc",
            "markdown-it-py",
            "mdurl",
            "pygments",
        )
        constraints.write_text("\n".join(f"{name}=={distribution(name).version}" for name in names))
        args += ["--constraint", str(constraints)]
    args.append(str(wheel) + ("[examples]" if extra else ""))
    command(args, workspace)

    return python


def inspect_wheel(wheel: Path) -> dict[str, str]:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        required = {
            "pysx/py.typed",
            "pysx/static/client.js",
            "pysx/static/index.html",
            "pysx/cli.py",
            "pysx/lsp.py",
            "pysx/_example_runner.py",
            "examples/counter.py",
        }

        if not required <= set(names):
            raise ProofError(f"missing artifact members: {required - set(names)}")
        entrypoint = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        entries = archive.read(entrypoint).decode()

        if "run_example:" in entries or "pysx._example_runner:app" not in entries:
            raise ProofError("entrypoint targets an excluded root module")

        return {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in names
            if not name.endswith("/RECORD")
        }


def framed(message: Mapping[str, object]) -> bytes:
    payload = json.dumps(message).encode()

    return f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload


def stdio_probe(python: Path, consumer: Path) -> dict[str, object]:
    messages: list[dict[str, object]] = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "shutdown"},
        {"jsonrpc": "2.0", "method": "exit"},
    ]
    result = subprocess.run(
        [str(python), "-I", "-m", "pysx.lsp", "--stdio"],
        cwd=consumer,
        env=clean_env(),
        input=b"".join(framed(message) for message in messages),
        capture_output=True,
        timeout=15,
        check=False,
    )

    if result.returncode != 0 or result.stderr:
        raise ProofError(f"stdio server failed: {result.stderr!r}")
    remaining = result.stdout
    responses: list[dict[str, object]] = []

    while remaining:
        header, remaining = remaining.split(b"\r\n\r\n", 1)
        count = int(header.split(b":", 1)[1])
        raw: object = json.loads(remaining[:count])

        if not isinstance(raw, dict):
            raise ProofError("invalid response shape")
        responses.append(cast("dict[str, object]", raw))
        remaining = remaining[count:]

    if len(responses) != 2 or responses[1]["result"] is not None:
        raise ProofError("missing initialize/shutdown responses")
    response = responses[0]
    initialized = cast("dict[str, object]", response["result"])
    info = cast("dict[str, object]", initialized["serverInfo"])
    proof = cast("dict[str, object]", initialized["proof"])

    if info["name"] != "pysx-architecture-proof" or proof["transport"] != "stdio":
        raise ProofError("installed server returned unexpected identity")

    if proof["isolated"] is not True or proof["executable"] != str(python):
        raise ProofError("installed server is not isolated")

    if any(str(ROOT) in str(value) for value in cast("list[str]", proof["sys_path"])):
        raise ProofError("checkout leaked onto stdio server path")

    return response


def portable_extension(workspace: Path) -> tuple[Path, Path]:
    extension = workspace / "portable-extension"
    extension.mkdir()
    manifest = {
        "name": "pysx-lang",
        "publisher": "pysx-local",
        "version": "0.0.1",
        "engines": {"vscode": "^1.91.0"},
        "main": "./extension.js",
        "activationEvents": ["onCommand:pysxProof.launch"],
        "contributes": {
            "commands": [{"command": "pysxProof.launch", "title": "Pysx launch proof"}],
            "configuration": {
                "title": "Pysx proof",
                "properties": {"pysxProof.python": {"type": "string", "default": ""}},
            },
        },
    }
    (extension / "package.json").write_text(json.dumps(manifest))
    (extension / "extension.js").write_text(EXTENSION_SOURCE)
    vsix = workspace / "pysx-launch-proof.vsix"
    with zipfile.ZipFile(vsix, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "extension.vsixmanifest",
            """<?xml version="1.0"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
<Metadata><Identity Language="en-US" Id="pysx-lang" Version="0.0.1" Publisher="pysx-local"/>
<DisplayName>pysx proof</DisplayName>
<Description xml:space="preserve">Isolated launch proof</Description>
</Metadata><Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation>
<Dependencies/><Assets><Asset Type="Microsoft.VisualStudio.Code.Manifest"
Path="extension/package.json" Addressable="true"/></Assets></PackageManifest>""",
        )
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="json" ContentType="application/json"/>
<Default Extension="js" ContentType="application/javascript"/>
<Default Extension="vsixmanifest" ContentType="text/xml"/></Types>""",
        )

        for file in extension.iterdir():
            archive.write(file, "extension/" + file.name)
    with zipfile.ZipFile(vsix) as archive:
        if any("pysx-root.json" in name or "node_modules" in name for name in archive.namelist()):
            raise ProofError("portable artifact contains forbidden checkout/build dependency")

        if str(ROOT).encode() in archive.read("extension/extension.js"):
            raise ProofError("portable launcher contains checkout path")

    return extension, vsix


def prepare_editor_fixture(workspace: Path) -> dict[str, str]:
    workspace.mkdir(parents=True, exist_ok=True)
    wheel, _, _, cache = build_artifacts(workspace)
    python = installed(workspace, wheel, cache, extra=False)
    consumer = workspace / "consumer"
    consumer.mkdir()
    stdio_probe(python, consumer)
    extension, vsix = portable_extension(workspace)

    return {
        "python": str(python),
        "extension": str(extension),
        "vsix": str(vsix),
        "consumer": str(consumer),
    }


def serve_probe(python: Path, consumer: Path) -> None:
    import socket

    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    executable = python.parent / ("pysx.exe" if os.name == "nt" else "pysx")
    child = subprocess.Popen(
        [str(executable), "--app", "pysx._distribution_demo:app", "--port", str(port)],
        cwd=consumer,
        env=clean_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        for _ in range(100):
            if child.poll() is not None:
                _, stderr = child.communicate()

                raise ProofError(f"installed serve exited: {stderr!r}")

            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                    if response.status != 200 or b"client.js" not in response.read():
                        raise ProofError("installed host did not serve assets")

                return
            except urllib.error.URLError, TimeoutError:
                time.sleep(0.05)

        raise ProofError("installed serve failed to become ready")
    finally:
        child.terminate()

        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)

        if child.stdout is not None:
            child.stdout.close()

        if child.stderr is not None:
            child.stderr.close()


def typing_probe(python: Path, consumer: Path) -> int:
    positive = consumer / "positive.py"
    positive.write_text(
        "from typing import assert_type\nfrom pysx import Signal, signal\n"
        "count = signal(1)\nassert_type(count, Signal[int])\n"
        "assert_type(count.get(), int)\nreveal_type(count)\nreveal_type(count.get())\n"
    )
    negative = consumer / "negative.py"
    negative.write_text('from pysx import signal\ncount = signal(1)\ncount.set("wrong")\n')
    config = consumer / "pyrightconfig.json"
    config.write_text(json.dumps({"typeCheckingMode": "strict", "pythonVersion": "3.14"}))

    for checker in ("mypy", "pyright"):
        args = [sys.executable, "-m", checker]

        if checker == "mypy":
            args += [
                "--strict",
                "--show-error-codes",
                "--no-incremental",
                "--python-executable",
                str(python),
                "--cache-dir",
                str(consumer / "mypy-cache"),
            ]
        else:
            args += ["--pythonpath", str(python), "--project", str(config)]
        result = command([*args, str(positive)], consumer)

        if "Signal[int]" not in result.stdout or '"int"' not in result.stdout:
            raise ProofError(f"{checker}: installed type inference is missing: {result.stdout!r}")
        invalid = command([*args, str(negative)], consumer, expected=1)
        code = "[arg-type]" if checker == "mypy" else "reportArgumentType"

        if code not in invalid.stdout or negative.name not in invalid.stdout:
            raise ProofError(
                f"{checker}: installed invalid payload rejected for wrong reason; "
                f"wanted {code} attributed to {negative.name}: {invalid.stdout!r}"
            )

    return 4


def qualify(workspace: Path) -> dict[str, str]:
    workspace.mkdir(parents=True, exist_ok=True)
    wheel, sdist, derived_wheel, cache = build_artifacts(workspace)

    if inspect_wheel(wheel) != inspect_wheel(derived_wheel):
        raise ProofError("sdist-derived artifact content diverges")
    core = installed(workspace, wheel, cache, extra=False)
    extra = installed(workspace, derived_wheel, cache, extra=True)
    consumer = workspace / "consumer"
    consumer.mkdir()
    script = """import importlib.metadata, importlib.resources, json, sys
import pysx as pysx_package, examples
from pathlib import Path
from pysx import Signal, signal, pysx
assert signal(1).get() == 1
assert Signal(2).get() == 2
assert pysx(t"div: installed") is not None
assert importlib.resources.files("pysx").joinpath("py.typed").is_file()
assert importlib.resources.files("pysx").joinpath("static/client.js").is_file()
assert "counter" in examples.examples
print(json.dumps({"package": str(Path(pysx_package.__file__).resolve()),
                  "paths": sys.path, "version": importlib.metadata.version("pysx")}))
"""

    for python in (core, extra):
        output: object = json.loads(command([str(python), "-I", "-c", script], consumer).stdout)
        report = cast("dict[str, object]", output)

        if not str(report["package"]).startswith(str(python.parent.parent)):
            raise ProofError("consumer imported an editable/source package")

        if any(str(ROOT) in str(path) for path in cast("list[str]", report["paths"])):
            raise ProofError("checkout leaked onto consumer sys.path")
        stdio_probe(python, consumer)
        typing_probe(python, consumer)
        serve_probe(python, consumer)
    example = "example.exe" if os.name == "nt" else "example"
    missing = command([str(core.parent / example), "--help"], consumer, expected=1)

    if "pysx[examples]" not in missing.stderr:
        raise ProofError("missing optional dependency is not actionable")
    command([str(extra.parent / example), "--help"], consumer)
    listing = command([str(extra.parent / example), "run"], consumer, expected=2)

    if "counter" not in listing.stderr or "todos" not in listing.stderr:
        raise ProofError("installed example registry is incomplete")
    # Unmodified root command still works with the example extra and copied layout.
    command([str(extra), str(workspace / "candidate" / "run_example.py"), "--help"], consumer)
    metadata = tomllib.loads((workspace / "candidate" / "pyproject.toml").read_text())

    if metadata["tool"]["uv"]["build-backend"]["module-name"] != ["pysx", "examples"]:
        raise ProofError("root-layout supported backend configuration lost")
    extension, vsix = portable_extension(workspace)
    qualification = {
        "backend": "uv_build==0.11.8",
        "layout": ["pysx", "examples"],
        "python": sys.version.split()[0],
        "artifacts": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (wheel, sdist, derived_wheel, vsix)
        },
        "wheel_members": len(inspect_wheel(wheel)),
        "strict_consumer_cases": 8,
        "installed_hosts": 2,
        "stdio_handshakes": 2,
        "example_extra": EXAMPLES_EXTRA,
        "status": "passed",
    }
    (workspace / "qualification-report.json").write_text(json.dumps(qualification, indent=2))

    return {
        "wheel": str(wheel),
        "sdist": str(sdist),
        "sdist_wheel": str(derived_wheel),
        "python": str(core),
        "examples_python": str(extra),
        "extension": str(extension),
        "vsix": str(vsix),
        "consumer": str(consumer),
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("give an isolated output directory")
    sys.stdout.write(json.dumps(prepare_editor_fixture(Path(sys.argv[1]))) + "\n")
