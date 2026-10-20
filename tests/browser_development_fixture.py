"""Exercise disposable copies of the shipped development example."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from pysx.development import Supervisor, WatchConfig

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection
    from websockets.http11 import Request, Response


class Fixture(Supervisor):
    async def http(self, _: ServerConnection, request: Request) -> Response | None:
        parsed = urlsplit(request.path)

        if parsed.path == "/__fixture__/persistence":
            stores = list(self.config.roots[0].rglob("*.sqlite3"))

            return self.response(200, b"unexpected store" if stores else b"no store")

        if parsed.path == "/__fixture__/edit":
            source = self.config.roots[0] / "examples" / "development.py"
            content = source.read_text()

            action = parse_qs(parsed.query).get("action")

            if action == ["initializer"]:
                content = content.replace("count = signal(0)", "count = signal(1)")
            elif action == ["restore"]:
                content = content.replace("count = signal(1)", "count = signal(0)")
            else:
                before, after = (
                    ("Edited live", "Hot reload")
                    if 'Title: "Edited live"' in content
                    else ("Hot reload", "Edited live")
                )
                content = content.replace(f'Title: "{before}"', f'Title: "{after}"')
            source.write_text(content)

            return self.response(200, b"edited")

        return await super().http(_, request)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--no-inspect", action="store_true")
    args = parser.parse_args()
    original = Path.cwd()

    with tempfile.TemporaryDirectory(prefix="pysx-development-example-") as directory:
        root = Path(directory)
        shutil.copytree(
            original / "examples", root / "examples", ignore=shutil.ignore_patterns("__pycache__")
        )
        os.environ["PYTHONPATH"] = str(original)
        os.chdir(root)

        try:
            asyncio.run(
                Fixture(
                    WatchConfig(
                        "examples.development:app",
                        (root,),
                        interval=0.05,
                        inspect=not args.no_inspect,
                    )
                ).run("127.0.0.1", args.port)
            )
        finally:
            os.chdir(original)


if __name__ == "__main__":
    main()
