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
from typing import cast

from acceptance_support import stop_process
from prototypes.distribution import ROOT, clean_env
from test_dev_server import wait_for


def _console(python: Path, name: str) -> str:
    suffix = ".exe" if sys.platform == "win32" else ""

    return str(python.with_name(name + suffix))


def _checked(
    command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(command)} exited {result.returncode}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )

    return result


class TutorialWorker:
    def __init__(self, workspace: Path, *, packaged: bool = False, editor: bool = False) -> None:
        self.workspace = workspace
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.process: subprocess.Popen[str] | None = None
        self.playground_process: subprocess.Popen[str] | None = None
        self.playground_port = 0
        self.playground_name = ""
        self.playground_original = ""
        self.schema = 1
        uv = shutil.which("uv")

        if uv is None:
            raise RuntimeError("uv is required for installed course verification")
        artifacts = workspace / "candidate"
        _checked(
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
        )
        self.site = workspace / "application"
        shutil.copytree(
            ROOT / "tutorial",
            self.site,
            ignore=shutil.ignore_patterns(".venv", ".artifacts", "__pycache__"),
        )
        _checked(
            [
                sys.executable,
                str(self.site / "bootstrap.py"),
                "--wheel",
                str(artifacts / "pysx_framework-0.1.0-py3-none-any.whl"),
            ],
            cwd=workspace,
            env=clean_env(),
        )
        self.python = (
            self.site / ".venv/Scripts/python.exe"
            if sys.platform == "win32"
            else self.site / ".venv/bin/python"
        )
        self.wheel: Path | None = None

        if packaged:
            _checked([uv, "build", str(self.site), "--wheel", "--out-dir", str(artifacts)])
            self.wheel = artifacts / "pysx_tutorial-0.1.0-py3-none-any.whl"
            self.reinstall()

        if editor:
            _checked(
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(self.python),
                    str(artifacts / "pysx_framework-0.1.0-py3-none-any.whl") + "[lsp]",
                ]
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
            _checked(
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
                    _console(self.python, "pysx"),
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
        self.stop_playground()

        if self.process is not None:
            if crash:
                self.process.kill()
                self.process.wait(timeout=10)
            else:
                stop_process(self.process)
            self.process = None

    def stop_playground(self) -> None:
        if self.playground_process is not None:
            stop_process(self.playground_process)
            self.playground_process = None

    def start_playground(self, name: str, *, scaffold: bool = True) -> str:
        if name not in ("development", "capstone"):
            raise ValueError("unknown playground")
        self.stop_playground()
        directory = self.workspace / "exercises"
        directory.mkdir(exist_ok=True)
        self.playground_name = name

        if scaffold:
            subprocess.run(
                [_console(self.python, "pysx-playground"), name],
                cwd=directory,
                env=clean_env(),
                capture_output=True,
                check=True,
            )
            self.playground_original = (directory / "playgrounds" / f"{name}.py").read_text()
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                self.playground_port = listener.getsockname()[1]
        command = [
            _console(self.python, "pysx"),
            "--app",
            f"playgrounds.{name}",
            "--port",
            str(self.playground_port),
        ]

        if name == "development":
            command.append("--watch")
        log = self.workspace / "playground.log"
        with log.open("w") as stream:
            self.playground_process = subprocess.Popen(
                command,
                cwd=directory,
                env=clean_env(),
                stdout=stream,
                stderr=stream,
                text=True,
                start_new_session=True,
            )
        wait_for(lambda: "pysx ready" in log.read_text(), log)

        return f"http://127.0.0.1:{self.playground_port}"

    def edit_playground(self, action: str) -> list[object]:
        if self.playground_name != "development":
            raise ValueError("development playground required")
        content = self.playground_original.replace(
            'h1: "Development playground"', f'h1: "Edited development playground: {action}"'
        )

        if action == "eager":
            content = content.replace('span(id="count"): {count}', 'span(id="count"): {count()}')
        elif action == "attribute":
            content = content.replace('title="A derived value"', 'tittle="A derived value"')
        elif action == "broken":
            content += "\ninvalid syntax !!!\n"
        elif action not in ("heading", "repair"):
            raise ValueError("unknown source edit")
        source = self.workspace / "exercises/playgrounds/development.py"
        source.write_text(content)
        result = subprocess.run(
            [str(self.python), "-m", "pysx.tooling.check", str(source)],
            cwd=source.parent.parent,
            env=clean_env(),
            capture_output=True,
            text=True,
            check=True,
        )
        rows: object = json.loads(result.stdout)

        if not isinstance(rows, list):
            raise ValueError("invalid diagnostic response")

        return cast("list[object]", rows)


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
                    response: dict[str, object] = {"base": f"http://127.0.0.1:{worker.port}"}

                    if self.path.startswith("/playground/start/"):
                        response["base"] = worker.start_playground(self.path.rsplit("/", 1)[-1])
                    elif self.path == "/playground/restart":
                        response["base"] = worker.start_playground(
                            worker.playground_name, scaffold=False
                        )
                    elif self.path == "/playground/stop":
                        worker.stop_playground()
                    elif self.path.startswith("/playground/edit/"):
                        response["diagnostics"] = worker.edit_playground(
                            self.path.rsplit("/", 1)[-1]
                        )
                    elif self.path in ("/restart", "/upgrade", "/reset"):
                        worker.stop(crash=self.path != "/reset")
                        worker.schema = 2 if self.path == "/upgrade" else 1

                        if self.path == "/upgrade":
                            worker.reinstall()
                        worker.start()
                    elif self.path != "/status":
                        raise ValueError("unknown control")
                    body = json.dumps(response).encode()
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
