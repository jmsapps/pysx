"""Test-owned local controls for actual tutorial worker loss and schema upgrade."""

import argparse
import json
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event, Thread

from acceptance_support import stop_process
from prototypes.distribution import ROOT, clean_env
from test_dev_server import wait_for


class TutorialWorker:
    def __init__(self, workspace: Path, *, packaged: bool = False, editor: bool = False) -> None:
        self.workspace = workspace
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.process: subprocess.Popen[str] | None = None
        self.schema = 1
        uv = shutil.which("uv")

        if uv is None:
            raise RuntimeError("uv is required for installed course verification")
        artifacts = workspace / "candidate"
        subprocess.run(
            [
                uv,
                "build",
                str(ROOT),
                "--wheel",
                "--out-dir",
                str(artifacts),
                "--python",
                sys.executable,
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.site = workspace / "application"
        shutil.copytree(
            ROOT / "tutorial",
            self.site,
            ignore=shutil.ignore_patterns(".venv", ".artifacts", "__pycache__"),
        )
        subprocess.run(
            [
                sys.executable,
                str(self.site / "bootstrap.py"),
                "--wheel",
                str(artifacts / "pysx-0.1.0-py3-none-any.whl"),
            ],
            cwd=workspace,
            env=clean_env(),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.python = self.site / ".venv/bin/python"
        self.wheel: Path | None = None

        if packaged:
            subprocess.run(
                [uv, "build", str(self.site), "--wheel", "--out-dir", str(artifacts)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.wheel = artifacts / "pysx_tutorial-0.1.0-py3-none-any.whl"
            self.reinstall()

        if editor:
            subprocess.run(
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(self.python),
                    str(artifacts / "pysx-0.1.0-py3-none-any.whl") + "[lsp]",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        probe = subprocess.run(
            [
                str(self.python),
                "-I",
                "-c",
                "import pathlib,pysx; "
                "assert pathlib.Path(pysx.__file__).is_relative_to("
                "pathlib.Path(__import__('sys').prefix))",
            ],
            cwd=self.site,
            env=clean_env(),
            capture_output=True,
            text=True,
            check=False,
        )

        if probe.returncode:
            raise RuntimeError(probe.stderr)

    def reinstall(self) -> None:
        if self.wheel is not None:
            subprocess.run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(self.python),
                    "--no-deps",
                    "--reinstall-package",
                    "pysx-tutorial",
                    str(self.wheel),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

    def start(self) -> None:
        log = self.workspace / "worker.log"
        environment = clean_env()
        environment.update(
            {
                "TUTORIAL_STATE_DIR": str(self.workspace / "state"),
                "TUTORIAL_LAB_SCHEMA": str(self.schema),
            }
        )
        with log.open("w") as stream:
            self.process = subprocess.Popen(
                [
                    str(self.python.with_name("pysx")),
                    "--app",
                    "src:app",
                    "--port",
                    str(self.port),
                ],
                cwd=self.workspace if self.wheel else self.site,
                env=environment,
                stdout=stream,
                stderr=stream,
                text=True,
                start_new_session=True,
            )
        wait_for(lambda: "pysx ready" in log.read_text(), log)

    def stop(self, *, crash: bool = False) -> None:
        if self.process is not None:
            if crash:
                self.process.kill()
                self.process.wait(timeout=10)
            else:
                stop_process(self.process)
            self.process = None


def run(port: int, *, packaged: bool = False) -> None:
    stopping = Event()

    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda _signal, _frame: stopping.set())
    with tempfile.TemporaryDirectory(prefix="pysx-course-") as directory:
        worker = TutorialWorker(Path(directory), packaged=packaged)
        worker.start()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

            def do_GET(self) -> None:
                try:
                    if self.path in ("/restart", "/upgrade", "/reset"):
                        worker.stop(crash=self.path != "/reset")
                        worker.schema = 2 if self.path == "/upgrade" else 1

                        if self.path == "/upgrade":
                            worker.reinstall()
                        worker.start()
                    elif self.path != "/status":
                        raise ValueError("unknown control")
                    body = json.dumps({"base": f"http://127.0.0.1:{worker.port}"}).encode()
                    self.send_response(200)
                except Exception:
                    body = b"tutorial worker control failed"
                    self.send_response(500)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer(("127.0.0.1", port), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        sys.stdout.write(f"pysx ready -> http://127.0.0.1:{port}\n")
        sys.stdout.flush()

        try:
            stopping.wait()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            worker.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--packaged", action="store_true")
    arguments = parser.parse_args()
    run(arguments.port, packaged=arguments.packaged)
