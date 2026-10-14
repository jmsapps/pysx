"""Disposable local TLS reverse proxy and production-configured application."""

import argparse
import asyncio
import signal
import tempfile
from pathlib import Path

from browser_security_fixture import app, principal
from security_proxy import proxy

from pysx import Application, HostConfig, SecurityPolicy


async def run(port: int) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop.set)
    external = f"https://127.0.0.1:{port}"
    application = Application(
        app,
        config=HostConfig(
            base_path="/secure",
            production=True,
            secure_cookies=True,
            principal=principal,
            security=SecurityPolicy(
                origins=(external,),
                trusted_proxies=("127.0.0.1/32",),
                require_principal=True,
                authorize=lambda _request, _action, _target: True,
            ),
        ),
    )

    try:
        with tempfile.TemporaryDirectory(prefix="pysx-tls-") as directory:
            async with (
                application.listen(port=0) as backend,
                proxy(Path(directory), backend.sockets[0].getsockname()[1], port),
            ):
                print(f"pysx ready -> {external}/secure", flush=True)
                await stop.wait()
    finally:
        for name in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(name)
    assert not application.runtime.graphs
    assert not application.connections


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    asyncio.run(run(parser.parse_args().port))
