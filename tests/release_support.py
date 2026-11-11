"""Build and exercise actual distribution artifacts outside the checkout."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
import zipfile
from typing import TYPE_CHECKING

from acceptance_support import interrupt, receive, shutdown_status, stop_process
from prototypes.distribution import ROOT, clean_env, command, dependency_cache
from test_dev_server import wait_for
from websockets.asyncio.client import connect

if TYPE_CHECKING:
    from pathlib import Path


def build_release_artifacts(workspace: Path) -> tuple[Path, Path, Path]:
    uv = shutil.which("uv")
    assert uv is not None
    cache = dependency_cache(workspace / "cache")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    pins = sorted({pin for values in project["optional-dependencies"].values() for pin in values})
    # Provision once; every artifact install below is offline and independent.
    command(
        [
            uv,
            "--cache-dir",
            str(cache),
            "pip",
            "install",
            "--python",
            sys.executable,
            "--target",
            str(workspace / "cache-tools"),
            *pins,
        ],
        workspace,
        timeout=300,
    )
    args = [uv, "--offline", "--cache-dir", str(cache), "build", "--python", sys.executable]
    artifacts = workspace / "artifacts"
    command([*args, str(ROOT), "--wheel", "--sdist", "--out-dir", str(artifacts)], workspace)
    wheel = next(artifacts.glob("*.whl"))
    sdist = next(artifacts.glob("*.tar.gz"))
    unpacked = workspace / "unpacked"
    unpacked.mkdir()
    with tarfile.open(sdist) as archive:
        archive.extractall(unpacked, filter="data")
    source = next(unpacked.iterdir())
    assert (source / "run_example.py").is_file()
    assert (source / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    rebuilt = workspace / "rebuilt"
    command([*args, str(source), "--wheel", "--out-dir", str(rebuilt)], workspace)
    derived = next(rebuilt.glob("*.whl"))
    with zipfile.ZipFile(wheel) as first, zipfile.ZipFile(derived) as second:
        assert {name: first.read(name) for name in first.namelist()} == {
            name: second.read(name) for name in second.namelist()
        }
        names = set(first.namelist())
        assert {
            "pysx/py.typed",
            "pysx/static/client.js",
            "pysx/static/index.html",
            "pysx/tooling/examples.py",
            "examples/counter.py",
            "examples/components/development.js",
            "examples/components/favicon.png",
        } <= names
        assert not any(name.startswith(("tests/", "tutorial/", ".skills/")) for name in names)
        license_name = next(name for name in names if name.endswith("/licenses/LICENSE"))
        assert first.read(license_name) == (ROOT / "LICENSE").read_bytes()

    return wheel, derived, cache


def install_release(workspace: Path, wheel: Path, cache: Path, extra: str) -> Path:
    uv = shutil.which("uv")
    assert uv is not None
    environment = workspace / "venv"
    command([uv, "venv", "--python", sys.executable, str(environment)], workspace)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    command(
        [
            uv,
            "--offline",
            "--cache-dir",
            str(cache),
            "pip",
            "install",
            "--python",
            str(python),
            str(wheel) + (f"[{extra}]" if extra else ""),
        ],
        workspace,
    )

    return python


async def increment_counter(port: int) -> None:
    async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
        initial = await receive(ws)
        assert initial["t"] == "init"
        buttons = re.findall(r"<button([^>]*)>(.*?)</button>", initial["html"], re.DOTALL)
        attributes = next(attributes for attributes, body in buttons if "Increment" in body)
        handler = re.search(r'data-pysx-click="([^"]+)"', attributes)
        assert handler is not None
        await ws.send(json.dumps({"t": "event", "h": handler[1]}))
        patch = await receive(ws)
        assert patch["t"] == "patch"
        assert any(op["op"] == "text" and op.get("v") == "1" for op in patch["ops"])


def installed_counter(python: Path, consumer: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    console = python.with_name("pysx-example.exe" if sys.platform == "win32" else "pysx-example")
    output = consumer / "counter.log"
    with output.open("w") as log:
        process = subprocess.Popen(
            [str(console), "run", "counter", "--port", str(port)],
            cwd=consumer,
            env=clean_env(),
            stdout=log,
            stderr=log,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )

        try:
            wait_for(lambda: "pysx ready" in output.read_text(), output)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
                assert b"Increment" in response.read()
            asyncio.run(increment_counter(port))
            interrupt(process)
            assert shutdown_status(process.wait(timeout=10)) == 0, output.read_text()
        finally:
            if process.poll() is None:
                stop_process(process)
    assert process.poll() is not None
