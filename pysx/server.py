"""Packaged standalone and optional ASGI serving entry point."""

from __future__ import annotations

import argparse
import asyncio
import math
import signal
import sys
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .hosting import Application, HostConfig
from .loader import import_app
from .session import Session, close_session

if TYPE_CHECKING:
    from collections.abc import Callable
    from string.templatelib import Template
    from types import FrameType

    from .render import Fragment

__all__ = ["Session", "close_session", "main"]


def _load_app(spec: str, *, development: bool = False) -> Application:
    module_name, _, attr = spec.partition(":")
    sys.path.insert(0, str(Path.cwd()))
    value: object = getattr(import_app(module_name), attr or "app")

    if isinstance(value, Application):
        if development and not value.config.development:
            return Application(value.app, config=replace(value.config, development=True))

        return value

    if not callable(value):
        raise TypeError("app must be callable or an Application")

    return Application(
        cast("Callable[[], Template | Fragment]", value), config=HostConfig(development=development)
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="pysx")
    parser.add_argument("--app", required=True, help="module:attr of an app or Application")
    parser.add_argument("--port", type=int, default=8750)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--dev", action="store_true", help="log development source tracebacks")
    parser.add_argument("--asgi", action="store_true", help="serve through the optional ASGI host")
    args = parser.parse_args()
    application = _load_app(args.app, development=args.dev)

    if args.asgi:
        try:
            import uvicorn

            from ._uvicorn import websocket_protocol
        except ImportError:
            raise SystemExit("ASGI serving requires pysx[asgi]; install that extra.") from None
        uvicorn.run(
            application.asgi(),
            host=args.host,
            port=args.port,
            ws=websocket_protocol(application.config.transport),
            ws_max_size=application.config.transport.max_frame_bytes,
            ws_max_queue=application.config.transport.socket_queue,
            ws_ping_interval=application.config.transport.ping_interval,
            ws_ping_timeout=application.config.transport.ping_timeout,
            timeout_graceful_shutdown=math.ceil(application.config.shutdown_timeout),
            loop="asyncio",
            lifespan="on",
            proxy_headers=False,
            access_log=False,
        )

        return

    async def run() -> None:
        loop = asyncio.get_running_loop()
        stopping = asyncio.Event()
        installed: list[
            tuple[signal.Signals, int | Callable[[int, FrameType | None], object] | None]
        ] = []

        for name in (signal.SIGINT, signal.SIGTERM):
            previous = signal.getsignal(name)

            try:
                loop.add_signal_handler(name, stopping.set)
            except NotImplementedError:
                signal.signal(name, lambda _signal, _frame: loop.call_soon_threadsafe(stopping.set))
            installed.append((name, previous))

        try:
            async with application.listen(args.host, args.port):
                print(f"pysx ready -> http://{args.host}:{args.port}", flush=True)
                await stopping.wait()
        finally:
            for name, previous in installed:
                with suppress(NotImplementedError):
                    loop.remove_signal_handler(name)
                signal.signal(name, previous)

    try:
        asyncio.run(run())
    except OSError as error:
        raise SystemExit(f"cannot bind {args.host}:{args.port}: {error}") from error
    except KeyboardInterrupt:
        pass
    except Exception:
        if application.config.production:
            raise SystemExit("application failed") from None

        raise


if __name__ == "__main__":
    main()
