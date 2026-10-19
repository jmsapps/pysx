"""Disposable app sources controlled only by this browser acceptance fixture."""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from test_dev_server import project

from pysx.development import Supervisor, WatchConfig

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection
    from websockets.http11 import Request, Response


class Fixture(Supervisor):
    async def http(self, _: ServerConnection, request: Request) -> Response | None:
        parsed = urlsplit(request.path)

        if parsed.path == "/__fixture__/edit":
            action = parse_qs(parsed.query).get("action", ["repair"])[0]
            root = self.config.roots[0]
            source = (
                'raise RuntimeError("broken browser edit")\n'
                if action == "break"
                else f'def label():\n    return "{action}"\n'
            )
            (root / "component.py").write_text(source)

            return self.response(200, b"edited")

        return await super().http(_, request)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    original = Path.cwd()

    with tempfile.TemporaryDirectory(prefix="pysx-browser-dev-") as directory:
        root = Path(directory)
        project(root)
        os.environ["PYTHONPATH"] = str(original)
        os.chdir(root)

        try:
            asyncio.run(
                Fixture(WatchConfig("user_app:app", (root,), interval=0.05)).run(
                    "127.0.0.1", args.port
                )
            )
        finally:
            os.chdir(original)


if __name__ == "__main__":
    main()
