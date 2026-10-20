"""Opt-in inspector over the real hot-reload workflow."""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from browser_hot_fixture import Fixture as HotFixture
from test_hot_reload import hot_project

from pysx.development import WatchConfig

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection
    from websockets.http11 import Request, Response


class Fixture(HotFixture):
    async def http(self, _: ServerConnection, request: Request) -> Response | None:
        if urlsplit(request.path).path == "/__fixture__/diagnostic":
            (self.config.roots[0] / "advisory.py").write_text(
                "from pysx import pysx\ndef example():\n"
                "    return pysx(t'div(noSuchAttribute=\"x\")')\n"
            )

            return self.response(200, b"edited")

        return await super().http(_, request)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    original = Path.cwd()

    with tempfile.TemporaryDirectory(prefix="pysx-inspector-") as directory:
        root = Path(directory)
        hot_project(root)
        source = root / "user_app.py"
        source.write_text(
            source.read_text().replace(
                '{"count": 0, "draft": "initial"}',
                '{"count": 0, "draft": "initial", "secret": "sensitive-inspector-token"}',
            )
        )
        os.environ["PYTHONPATH"] = str(original)
        os.chdir(root)

        try:
            asyncio.run(
                Fixture(WatchConfig("user_app:app", (root,), interval=0.05, inspect=True)).run(
                    "127.0.0.1", args.port
                )
            )
        finally:
            os.chdir(original)


if __name__ == "__main__":
    main()
