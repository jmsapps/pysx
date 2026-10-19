"""Packaged standalone and optional ASGI serving entry point."""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import signal
import sys
import time
from contextlib import nullcontext, suppress
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .durable_host import DurableApplication
from .hosting import Application, HostConfig
from .loader import import_app
from .session import Session, close_session

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager
    from string.templatelib import Template
    from types import FrameType

    from .render import Fragment

__all__ = ["Session", "close_session", "main"]


def _drain_handler(application: Application) -> Callable[[], None] | None:
    if isinstance(application, DurableApplication):
        return application.begin_drain

    return None


def _load_app(spec: str, *, development: bool = False) -> Application:
    module_name, _, attr = spec.partition(":")
    sys.path.insert(0, str(Path.cwd()))
    value: object = getattr(import_app(module_name), attr or "app")

    if isinstance(value, Application):
        if development and not value.config.development:
            value.config = replace(value.config, development=True)
            value.runtime.development = True

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
    parser.add_argument(
        "--watch", action="store_true", help="watch local sources and reload browsers"
    )
    parser.add_argument(
        "--watch-root", action="append", type=Path, help="source directory (repeatable)"
    )
    parser.add_argument("--watch-interval", type=float, default=0.2)
    parser.add_argument("--watch-debounce", type=float, default=0.15)
    parser.add_argument(
        "--no-preserve-state",
        action="store_true",
        help="reload browsers instead of preserving component-local state",
    )
    parser.add_argument("--reload-timeout", type=float, default=10)
    parser.add_argument(
        "--inspect", action="store_true", help="opt-in redacted local runtime inspector"
    )
    parser.add_argument(
        "--drain-signal", action="store_true", help="drain durable admission on SIGUSR1"
    )
    args = parser.parse_args()

    if args.watch:
        from .development import WatchConfig
        from .development import run as watch

        if args.asgi or args.drain_signal:
            parser.error(
                "--watch uses its own local host; --asgi/--drain-signal cannot be combined"
            )

        try:
            watch(
                WatchConfig(
                    args.app,
                    tuple(root.resolve() for root in (args.watch_root or [Path.cwd()])),
                    interval=args.watch_interval,
                    debounce=args.watch_debounce,
                    timeout=args.reload_timeout,
                    inspect=args.inspect,
                    preserve=not args.no_preserve_state,
                ),
                args.host,
                args.port,
            )
        except (OSError, ValueError) as error:
            raise SystemExit(f"cannot start development server: {error}") from error
        except KeyboardInterrupt:
            pass

        return

    if args.inspect:
        parser.error("--inspect requires --watch")
    import_started = time.perf_counter()
    application = _load_app(args.app, development=args.dev)
    import_seconds = time.perf_counter() - import_started

    if args.drain_signal:
        # SIGUSR1 does not exist on every supported platform, so the attribute is
        # resolved dynamically rather than named where a checker would resolve it
        # against the running platform's signal module.
        drain = cast("signal.Signals | None", getattr(signal, "SIGUSR1", None))

        begin_drain = _drain_handler(application)

        if begin_drain is None or drain is None:
            raise SystemExit("--drain-signal requires a durable application and SIGUSR1 support")
        signal.signal(drain, lambda _signal, _frame: begin_drain())

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
            control: AbstractAsyncContextManager[None] = nullcontext()

            control_port = os.environ.get("PYSX_DEV_CONTROL_PORT") if args.dev else None

            if control_port:
                from .development_control import control_server

                token = os.environ.get("PYSX_DEV_CONTROL_TOKEN")

                if not token:
                    raise SystemExit(
                        "PYSX_DEV_CONTROL_PORT requires PYSX_DEV_CONTROL_TOKEN; set both or neither"
                    )

                if not control_port.isdigit():
                    raise SystemExit(
                        f"PYSX_DEV_CONTROL_PORT must be a port number, got {control_port!r}"
                    )

                control = control_server(
                    application,
                    int(control_port),
                    token,
                    import_seconds=import_seconds,
                )

            async with control, application.listen(args.host, args.port):
                print(
                    f"pysx ready -> http://{args.host}:{args.port}{application.config.base_path}/",
                    flush=True,
                )
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
