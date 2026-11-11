"""Fresh wheel and sdist consumer evidence for the isolated packaging candidate."""

import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import distribution as metadata_distribution
from pathlib import Path
from types import SimpleNamespace

import pytest
from acceptance_support import interrupt, raised_signal_status, shutdown_status
from prototypes import distribution as distribution_fixture
from prototypes.distribution import (
    ROOT,
    clean_env,
    command,
    dependency_cache,
    installed,
    qualify,
    typing_probe,
)
from release_support import build_release_artifacts, install_release, installed_counter
from test_dev_server import get, project, serving, wait_for

from pysx.tooling.tools import RUFF_REQUIREMENT


def _console(python: Path, name: str) -> str:
    suffix = ".exe" if sys.platform == "win32" else ""

    return str(python.with_name(name + suffix))


@pytest.fixture(scope="module")
def stable_api_artifacts(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    return build_release_artifacts(tmp_path_factory.mktemp("stable-api-artifacts"))


@pytest.mark.parametrize("artifact", [0, 1], ids=["wheel", "sdist-wheel"])
@pytest.mark.parametrize(
    "extra",
    ["", "asgi", "dev", "lsp", "examples"],
    ids=["minimal", "asgi", "dev", "lsp", "examples"],
)
def test_stable_api_installed_consumers(
    tmp_path: Path, stable_api_artifacts: tuple[Path, Path, Path], artifact: int, extra: str
) -> None:
    python = install_release(
        tmp_path, stable_api_artifacts[artifact], stable_api_artifacts[2], extra
    )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    optional = {"uvicorn", "ruff", "pyright", "pygls", "typer"}
    expected = {
        "": set[str](),
        "asgi": {"uvicorn"},
        "dev": {"ruff"},
        "lsp": {"ruff", "pyright", "pygls"},
        "examples": {"typer"},
    }[extra]
    source = f"""
import importlib, importlib.metadata as metadata, importlib.resources as resources, sys
from pathlib import Path
import pysx
distribution = metadata.distribution("pysx")
assert distribution.version == {project["version"]!r}
assert distribution.metadata["License-Expression"] == "MIT"
assert distribution.metadata["Author-email"] == "JMS Apps <jmsapps@outlook.com>"
assert not any({str(ROOT)!r} in value for value in sys.path)
assert str(Path(pysx.__file__).resolve()).startswith({str(python.parent.parent)!r})
assert all(getattr(pysx, name) is not None for name in pysx.__all__)
assert resources.files("pysx").joinpath("py.typed").is_file()
present = {{item.metadata["Name"].lower() for item in metadata.distributions()}}
assert present & {optional!r} == {expected!r}, present
for entry in distribution.entry_points:
    if entry.name != "pysx-example" or {extra!r} == "examples":
        assert callable(entry.load())
if {extra!r} == "asgi":
    import uvicorn
if {extra!r} == "lsp":
    import pysx.tooling.lsp.server
"""
    command([str(python), "-I", "-c", source], tmp_path)

    if not extra:
        assert typing_probe(python, tmp_path) == 4

    for name in project["scripts"]:
        if name == "pysx-example" and extra != "examples":
            result = command([_console(python, name), "--help"], tmp_path, expected=1)
            assert "pysx[examples]" in result.stderr
        else:
            command([_console(python, name), "--help"], tmp_path)

    if extra in ("dev", "lsp"):
        script = (
            "from pysx.tooling.tools import resolve_ruff; "
            f"assert resolve_ruff().version == {RUFF_REQUIREMENT!r}"
        )
        command([str(python), "-I", "-c", script], tmp_path)
        source_file = tmp_path / "format_me.py"
        source_file.write_text("value=  1\n")
        command([str(python), "-I", "-m", "ruff", "format", str(source_file)], tmp_path)
        assert source_file.read_text() == "value = 1\n"

    if extra == "examples":
        installed_counter(python, tmp_path)


def test_installed_serve_fresh_wheel_outside_checkout(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    cache = dependency_cache(tmp_path / "cache")
    artifacts = tmp_path / "artifacts"
    command(
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
        tmp_path,
    )
    wheel = next(artifacts.glob("*.whl"))

    with zipfile.ZipFile(wheel) as archive:
        assert "pysx/static/development.js" in archive.namelist()
        assert "pysx/development/diagnostics.py" in archive.namelist()
    python = installed(tmp_path, wheel, cache, extra=False)
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    project(consumer)

    with serving(consumer, [_console(python, "pysx")]) as (port, log):
        assert b"original" in get(port)
        (consumer / "component.py").write_text('def label():\n    return "installed edit"\n')
        wait_for(lambda: b"installed edit" in get(port), log)


def test_fresh_installed_distribution(tmp_path: Path) -> None:
    report = qualify(tmp_path)
    assert Path(report["wheel"]).is_file()
    assert Path(report["sdist_wheel"]).is_file()
    assert Path(report["vsix"]).is_file()


def test_cache_preparation_supplies_constrained_transitive_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A compatible cached version must not mask the consumer's exact requirement."""
    uv = shutil.which("uv")
    assert uv is not None
    index = tmp_path / "index"
    packages = index / "simple" / "markdown-it-py"
    packages.mkdir(parents=True)

    # Synthetic versions avoid depending on registry releases or existing caches.
    for version in ("9000", "9001"):
        metadata = f"markdown_it_py-{version}.dist-info"
        with zipfile.ZipFile(packages / f"markdown_it_py-{version}-py3-none-any.whl", "w") as wheel:
            wheel.writestr(
                f"{metadata}/METADATA",
                f"Metadata-Version: 2.1\nName: markdown-it-py\nVersion: {version}\n",
            )
            wheel.writestr(
                f"{metadata}/WHEEL",
                "Wheel-Version: 1.0\nGenerator: fixture\n"
                "Root-Is-Purelib: true\nTag: py3-none-any\n",
            )
            wheel.writestr(f"{metadata}/RECORD", "")

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(index)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/simple"
    cache = tmp_path / "cache"
    original_command = distribution_fixture.command

    def indexed_command(
        args: list[str], cwd: Path, *, timeout: int = 60
    ) -> subprocess.CompletedProcess[str]:
        return original_command([*args, "--index-url", url], cwd, timeout=timeout)

    monkeypatch.setattr(distribution_fixture, "required_pins", lambda: ["markdown-it-py>=9000"])

    def selected_distribution(name: str) -> SimpleNamespace:
        return SimpleNamespace(
            version="9000" if name == "markdown-it-py" else metadata_distribution(name).version
        )

    monkeypatch.setattr(distribution_fixture, "distribution", selected_distribution)
    monkeypatch.setattr(distribution_fixture, "command", indexed_command)

    try:
        original_command(
            [
                uv,
                "--cache-dir",
                str(cache),
                "pip",
                "install",
                "--python",
                sys.executable,
                "--target",
                str(tmp_path / "seed"),
                "--index-url",
                url,
                "markdown-it-py==9001",
            ],
            tmp_path,
        )
        distribution_fixture.warmed(cache)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    # The index is now unavailable: preparation must already have fetched 9000.
    original_command(
        [
            uv,
            "--offline",
            "--cache-dir",
            str(cache),
            "pip",
            "install",
            "--python",
            sys.executable,
            "--target",
            str(tmp_path / "consumer"),
            "--index-url",
            url,
            "markdown-it-py==9000",
        ],
        tmp_path,
    )


@pytest.mark.parametrize("asgi", [False, True], ids=["standalone", "asgi"])
def test_host_config_fresh_actual_wheel_user_app(tmp_path: Path, *, asgi: bool) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    cache = dependency_cache(tmp_path / "cache")
    artifacts = tmp_path / "artifacts"
    command(
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
        tmp_path,
    )
    wheel = next(artifacts.glob("*.whl"))
    python = installed(tmp_path, wheel, cache, extra=False)
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    (consumer / "user_app.py").write_text(
        "from pysx import Application, HostConfig, StaticAsset, native\n"
        "from pathlib import Path\n"
        'def app():\n    return native.H1("Installed host content")\n'
        'host = Application(app, config=HostConfig(base_path="/prefix", '
        'assets={"/note.txt": StaticAsset.from_path(Path("note.txt"))}))\n'
    )
    (consumer / "note.txt").write_text("user asset")
    console = Path(_console(python, "pysx"))
    imports = command(
        [
            str(python),
            "-I",
            "-c",
            "import sys; from pysx import Application; import pysx.tooling.check; "
            "assert 'cryptography' not in sys.modules; "
            "import cryptography; assert cryptography.__version__ == '50.0.2'; "
            'assert "uvicorn" not in sys.modules; assert "pysx.hosting.server" not in sys.modules; '
            "import asyncio; "
            "assert asyncio._get_running_loop() is None",
        ],
        consumer,
    )
    assert imports.stdout == ""
    missing = command(
        [
            str(console),
            "--app",
            "user_app:host",
            "--asgi",
        ],
        consumer,
        expected=1,
    )
    assert "pysx[asgi]" in missing.stderr

    if asgi:
        # Only this environment receives the optional extra, from the actual wheel.
        command(
            [
                uv,
                "--cache-dir",
                str(cache),
                "pip",
                "install",
                "--python",
                str(python),
                str(wheel) + "[asgi]",
            ],
            tmp_path,
        )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    args = [str(console), "--app", "user_app:host", "--port", str(port)]

    if asgi:
        args.append("--asgi")
    output = tmp_path / "host.log"

    with output.open("w") as log:
        # interrupt() aims CTRL_BREAK_EVENT at this child on Windows, and that event
        # reaches the whole group: without its own group it kills the test runner.
        process = subprocess.Popen(
            args,
            cwd=consumer,
            env=clean_env(),
            stdout=log,
            stderr=log,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )

        try:
            deadline = time.monotonic() + 15

            while True:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/prefix/", timeout=1
                    ) as response:
                        body = response.read()
                        assert b"Installed host content" in body
                        assert b"/prefix/client.js" in body

                    break
                except urllib.error.URLError:
                    if time.monotonic() >= deadline or process.poll() is not None:
                        pytest.fail(output.read_text())
                    time.sleep(0.05)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/prefix/note.txt", timeout=2
            ) as response:
                assert response.read() == b"user asset"
                assert response.headers["Content-Type"] == "text/plain"
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/prefix/client.js", timeout=2
            ) as response:
                assert b"new WebSocket" in response.read()
            interrupt(process)
            status = shutdown_status(process.wait(timeout=8))

            if sys.platform == "win32" and asgi:
                import signal

                assert status == raised_signal_status(signal.SIGBREAK)
                assert "Application shutdown complete" in output.read_text()
            else:
                assert status == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_client_verify_dev_extra_is_separate_from_the_runtime_set() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = metadata["project"]
    runtime = project["dependencies"]
    extras = project["optional-dependencies"]
    assert runtime == ["cryptography==50.0.2", "websockets==17.1"]
    assert extras["dev"] == [f"ruff=={RUFF_REQUIREMENT}"]
    assert not any(item.startswith("ruff") for item in runtime)

    for name, items in extras.items():
        for item in items:
            assert "==" in item, f"{name} declares an unpinned requirement: {item}"


def test_client_verify_bundled_client_installs_outside_the_checkout(tmp_path: Path) -> None:
    built = subprocess.run(
        [sys.executable, str(ROOT / "editor" / "build_vsix.py")],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    staged = tmp_path / "pysx-lang.vsix"
    shutil.copy(built.stdout.strip(), staged)

    with zipfile.ZipFile(staged) as archive:
        names = archive.namelist()
        assert "extension/node_modules/vscode-languageclient/package.json" in names

        for name in names:
            if name.endswith((".js", ".json")):
                assert str(ROOT).encode() not in archive.read(name), name
