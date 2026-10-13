"""Packaged standalone and optional ASGI serving entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .hosting import Application, HostConfig
from .loader import import_app
from .session import Session, close_session

if TYPE_CHECKING:
    from collections.abc import Callable
    from string.templatelib import Template

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
        except ImportError:
            raise SystemExit("ASGI serving requires pysx[asgi]; install that extra.") from None
        uvicorn.run(
            application.asgi(),
            host=args.host,
            port=args.port,
            ws="websockets-sansio",
            loop="asyncio",
            lifespan="on",
        )

        return

    async def run() -> None:
        async with application.listen(args.host, args.port):
            print(f"pysx ready -> http://{args.host}:{args.port}", flush=True)
            await asyncio.get_running_loop().create_future()

    try:
        asyncio.run(run())
    except OSError as error:
        raise SystemExit(f"cannot bind {args.host}:{args.port}: {error}") from error
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
