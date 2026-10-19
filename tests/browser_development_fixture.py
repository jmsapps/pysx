"""Exercise disposable copies of the shipped development example."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pysx.development import Supervisor, WatchConfig

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection
    from websockets.http11 import Request, Response


class Fixture(Supervisor):
    async def http(self, _: ServerConnection, request: Request) -> Response | None:
        if urlsplit(request.path).path == "/__fixture__/edit":
            source = self.config.roots[0] / "examples" / "durable_state.py"
            content = source.read_text()
            before, after = (
                ("Edited live", "Durable state")
                if 'Title: "Edited live"' in content
                else ("Durable state", "Edited live")
            )
            source.write_text(content.replace(f'Title: "{before}"', f'Title: "{after}"'))

            return self.response(200, b"edited")

        return await super().http(_, request)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    original = Path.cwd()

    with tempfile.TemporaryDirectory(prefix="pysx-development-example-") as directory:
        root = Path(directory)
        shutil.copytree(
            original / "examples", root / "examples", ignore=shutil.ignore_patterns("__pycache__")
        )
        os.environ["PYTHONPATH"] = str(original)
        os.environ["PYSX_STATE_PATH"] = str(root / "state.sqlite3")
        os.chdir(root)

        try:
            asyncio.run(
                Fixture(
                    WatchConfig("examples.development:app", (root,), interval=0.05, inspect=True)
                ).run("127.0.0.1", args.port)
            )
        finally:
            os.chdir(original)


if __name__ == "__main__":
    main()
