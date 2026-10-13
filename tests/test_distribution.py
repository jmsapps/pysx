"""Fresh wheel and sdist consumer evidence for the isolated packaging candidate."""

import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from prototypes.distribution import ROOT, clean_env, command, dependency_cache, installed, qualify


def test_fresh_installed_distribution(tmp_path: Path) -> None:
    report = qualify(tmp_path)
    assert Path(report["wheel"]).is_file()
    assert Path(report["sdist_wheel"]).is_file()
    assert Path(report["vsix"]).is_file()


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
    console = python.with_name("pysx")
    imports = command(
        [
            str(python),
            "-I",
            "-c",
            "import sys; from pysx import Application; import pysx.check; "
            'assert "uvicorn" not in sys.modules; assert "pysx.server" not in sys.modules; '
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
        process = subprocess.Popen(args, cwd=consumer, env=clean_env(), stdout=log, stderr=log)

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
            process.send_signal(signal.SIGINT)
            assert process.wait(timeout=8) == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
