"""Loopback-only controls owning the disposable Linux acceptance topology."""

from __future__ import annotations

import argparse
import json
import signal
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event, Thread
from urllib.parse import parse_qs, urlsplit

from deployment_topology import Deployment


def run(port: int, *, example: bool = False) -> None:
    stopping = Event()

    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda _signal, _frame: stopping.set())

    with tempfile.TemporaryDirectory(prefix="pysx-deployment-") as directory:  # noqa: SIM117
        with Deployment(Path(directory), example=example) as topology:
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                    pass

                def do_GET(self) -> None:
                    parsed = urlsplit(self.path)
                    worker = parse_qs(parsed.query).get("worker", ["worker-a"])[0]

                    try:
                        if worker not in ("worker-a", "worker-b"):
                            raise ValueError("unknown worker")

                        if parsed.path == "/reset":
                            topology.command("restart", "worker-a", "worker-b")
                            topology.wait_ready()
                        elif parsed.path == "/kill":
                            topology.command("kill", "-s", "SIGKILL", worker)
                        elif parsed.path == "/drain":
                            topology.command("kill", "-s", "SIGUSR1", worker)
                            assert topology.probe(worker)[0] == 503
                        elif parsed.path == "/restart":
                            topology.command("restart", worker)
                            topology.wait_ready()
                        elif parsed.path != "/status":
                            raise ValueError("unknown control")
                        body = json.dumps({"base": topology.base}).encode()
                        self.send_response(200)
                    except Exception:
                        body = b"control failed"
                        self.send_response(500)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            server = HTTPServer(("127.0.0.1", port), Handler)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()

            try:
                print(f"pysx ready -> http://127.0.0.1:{port}", flush=True)
                stopping.wait()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--example", action="store_true")
    args = parser.parse_args()
    run(args.port, example=args.example)
