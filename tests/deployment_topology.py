"""Own disposable Linux containers, certificates and state for acceptance."""

from __future__ import annotations

import json
import os
import secrets
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Self

from ownership_topology import free_port

if TYPE_CHECKING:
    from types import TracebackType

ROOT = Path(__file__).resolve().parents[1]


def linux_containers() -> bool:
    """Whether this host can run the Linux images the reference topology needs."""

    try:
        found = subprocess.run(
            ["docker", "info", "--format", "{{.OSType}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return False

    return found.returncode == 0 and found.stdout.strip() == "linux"


class Deployment:
    def __init__(self, directory: Path, *, example: bool = False) -> None:
        self.directory = directory
        self.project = "pysx-acceptance-" + secrets.token_hex(5)
        self.port = free_port()
        self.base = f"https://127.0.0.1:{self.port}"
        self.environment = {
            **os.environ,
            "PYSX_APP": "examples.durable_state:app" if example else "tests.deployment_app:host",
            "PYSX_ORIGIN": self.base,
            "PYSX_PORT": str(self.port),
            "PYSX_TLS_CERT": str(directory / "cert.pem"),
            "PYSX_TLS_KEY": str(directory / "key.pem"),
        }

    def command(self, *arguments: str, timeout: float = 60) -> str:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                self.project,
                "-f",
                str(ROOT / "deploy/compose.yaml"),
                *arguments,
            ],
            cwd=ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

        if result.returncode:
            raise RuntimeError(result.stderr[-4000:] + result.stdout[-4000:])

        return result.stdout

    def inside(self, worker: str, code: str) -> str:
        if worker not in ("worker-a", "worker-b"):
            raise ValueError("unknown worker")

        return self.command(
            "exec",
            "-T",
            worker,
            "uv",
            "run",
            "--project",
            ".",
            "--no-dev",
            "python",
            "-c",
            code,
        )

    def probe(self, worker: str, path: str = "/_pysx/ready") -> tuple[int, str]:
        code = (
            "import urllib.request, urllib.error, json\n"
            "try:\n"
            f" r = urllib.request.urlopen('http://127.0.0.1:8750{path}', timeout=2)\n"
            "except urllib.error.HTTPError as e:\n r = e\n"
            "print(json.dumps([r.status, r.read().decode()]))\n"
        )
        status, body = json.loads(self.inside(worker, code))

        return int(status), str(body)

    def public(self, path: str) -> tuple[int, str]:
        context = ssl.create_default_context(cafile=self.environment["PYSX_TLS_CERT"])

        try:
            with urllib.request.urlopen(self.base + path, context=context, timeout=5) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def wait_ready(self) -> None:
        deadline = time.monotonic() + 30

        while time.monotonic() < deadline:
            try:
                if (
                    all(self.probe(worker)[0] == 200 for worker in ("worker-a", "worker-b"))
                    and self.public("/health")[0] == 200
                ):
                    return
            except OSError, RuntimeError, urllib.error.URLError:
                pass
            time.sleep(0.1)

        raise TimeoutError("Linux topology did not become ready")

    def __enter__(self) -> Self:
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=IP:127.0.0.1,DNS:localhost",
                "-keyout",
                self.environment["PYSX_TLS_KEY"],
                "-out",
                self.environment["PYSX_TLS_CERT"],
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )

        try:
            self.command("up", "-d", "--build", timeout=180)
            self.wait_ready()
        except BaseException:
            self.close()

            raise

        return self

    def capture(self) -> Path | None:
        """Persist container output before teardown destroys it.

        A failed upgrade or unhealthy worker leaves its only evidence inside the
        containers, and `close` removes them, so the logs are read out first and
        written somewhere that outlives the fixture's temporary directory.
        """
        destination = Path(os.environ.get("PYSX_DEPLOYMENT_LOGS") or ROOT / ".pysx-logs")

        try:
            destination.mkdir(parents=True, exist_ok=True)
            logs = self.command("logs", "--no-color", "--timestamps", timeout=30)
        except OSError, RuntimeError, subprocess.SubprocessError:
            return None
        target = destination / f"deployment-{self.project}.log"
        target.write_text(logs, encoding="utf-8")

        return target

    def close(self) -> None:
        self.capture()
        self.command("down", "--volumes", "--remove-orphans", "--timeout", "7", timeout=30)
        remaining = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={self.project}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )

        if remaining.stdout.strip():
            raise RuntimeError("owned deployment containers remained")

        for resource in ("network", "volume"):
            result = subprocess.run(
                [
                    "docker",
                    resource,
                    "ls",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={self.project}",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )

            if result.stdout.strip():
                raise RuntimeError(f"owned deployment {resource} remained")

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _error: BaseException | None,
        _trace: TracebackType | None,
    ) -> None:
        self.close()
