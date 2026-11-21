"""Run a real nginx in front of a PYSX application, with disposable TLS.

Everything lives under a temporary prefix: no system nginx configuration or service is
touched, and the generated key material never leaves the test directory.
"""

from __future__ import annotations

import os
import socket
import ssl
import subprocess
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.setup_proxy import nginx_binary, openssl_binary

if TYPE_CHECKING:
    from collections.abc import Generator

CONFIGURATION = """
worker_processes 1;
pid "{prefix}/nginx.pid";
error_log "{prefix}/error.log" warn;
events {{ worker_connections 64; }}
http {{
  access_log "{prefix}/access.log";
  client_body_temp_path "{prefix}/body";
  proxy_temp_path "{prefix}/proxy";
  fastcgi_temp_path "{prefix}/fastcgi";
  uwsgi_temp_path "{prefix}/uwsgi";
  scgi_temp_path "{prefix}/scgi";
  map $http_upgrade $connection_upgrade {{ default upgrade; '' close; }}

  server {{
    listen {port} ssl;
    http2 on;
    server_name localhost;
    ssl_certificate "{prefix}/tls.crt";
    ssl_certificate_key "{prefix}/tls.key";

    location / {{
      # No replacement URI, so the full request path reaches the application and a
      # mounted prefix still resolves.
      proxy_pass http://127.0.0.1:{upstream};
      proxy_http_version 1.1;
      # Incoming forwarding headers are discarded and only supported trusted values are
      # set, so a client cannot forge its own origin or protocol.
      proxy_set_header Forwarded "";
      proxy_set_header X-Forwarded-Host $host:{port};
      proxy_set_header X-Forwarded-Proto https;
      proxy_set_header X-Forwarded-For $remote_addr;
      proxy_set_header X-Forwarded-Port {port};
      proxy_set_header Host $host;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection $connection_upgrade;
      proxy_buffering off;
      proxy_read_timeout 75s;
    }}
  }}
}}
"""


def disposable_tls_client() -> ssl.SSLContext:
    """Trust the fixture's own generated certificate without touching system trust."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    return context


def free_port() -> int:
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))

        return int(probe.getsockname()[1])


def certificate(prefix: Path) -> None:
    openssl = openssl_binary()

    if openssl is None:
        raise RuntimeError("openssl is required to generate disposable TLS material")
    subprocess.run(
        [
            openssl,
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
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(prefix / "tls.key"),
            "-out",
            str(prefix / "tls.crt"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )


def _launch_diagnosis(command: list[str], prefix: Path, configuration: Path) -> str:
    """Separate "nginx cannot resolve this path" from "the fixture never wrote it"."""
    try:
        listing = ", ".join(sorted(entry.name for entry in prefix.iterdir()))
    except OSError as error:
        listing = f"unreadable: {error}"

    return (
        f"command: {command}\n"
        f"prefix: {prefix} (exists={prefix.is_dir()})\n"
        f"configuration: {configuration} (exists={configuration.is_file()})\n"
        f"prefix entries: {listing}"
    )


@contextmanager
def nginx(prefix: Path, upstream: int, *, port: int | None = None) -> Generator[int]:
    """Serve HTTPS on a disposable port, forwarding to an already running application."""
    executable = nginx_binary()

    if executable is None:
        raise RuntimeError("nginx is required for reverse-proxy qualification")
    selected = port or free_port()
    prefix.mkdir(parents=True, exist_ok=True)
    prefix = prefix.resolve()

    for name in ("body", "proxy", "fastcgi", "uwsgi", "scgi", "logs"):
        (prefix / name).mkdir(parents=True, exist_ok=True)
    certificate(prefix)
    configuration = prefix / "nginx.conf"
    configuration.write_text(
        CONFIGURATION.format(prefix=prefix.as_posix(), port=selected, upstream=upstream),
        encoding="utf-8",
    )
    command = [
        executable,
        "-p",
        prefix.as_posix(),
        "-c",
        configuration.as_posix(),
        "-g",
        "daemon off;",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        deadline = time.monotonic() + 30

        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"nginx exited: {(process.stderr.read() if process.stderr else b'').decode()}\n"
                    f"{_launch_diagnosis(command, prefix, configuration)}"
                )

            with closing(socket.socket()) as probe:
                probe.settimeout(0.5)

                if probe.connect_ex(("127.0.0.1", selected)) == 0:
                    break
            time.sleep(0.05)
        else:
            raise RuntimeError("nginx did not accept connections")

        yield selected
    finally:
        if process.poll() is None:
            subprocess.run(
                [*command[:5], "-s", "quit"],
                capture_output=True,
                timeout=10,
                check=False,
            )

        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@contextmanager
def application(target: str, *, environment: dict[str, str] | None = None) -> Generator[int]:
    """Start a real PYSX process, the way the documented deployment does."""
    import sys

    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "pysx.hosting.server", "--app", target, "--port", str(port)],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, **(environment or {})},
    )

    try:
        deadline = time.monotonic() + 60

        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = (process.stdout.read() if process.stdout else b"").decode()

                raise RuntimeError(f"application exited: {output}")

            with closing(socket.socket()) as probe:
                probe.settimeout(0.5)

                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        else:
            raise RuntimeError("application did not accept connections")

        yield port
    finally:
        process.terminate()

        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
