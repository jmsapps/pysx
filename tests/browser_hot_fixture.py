"""Disposable hot-reload application with bounded test-only edit controls."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from test_hot_reload import COMPONENT, HOT_SOURCE, hot_project

from pysx.development import Supervisor, WatchConfig

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection
    from websockets.http11 import Request, Response


class Fixture(Supervisor):
    async def http(self, _: ServerConnection, request: Request) -> Response | None:
        parsed = urlsplit(request.path)
        root = self.config.roots[0]

        if parsed.path == "/__fixture__/metrics":
            counts = {
                name: len((root / name).read_text().splitlines()) if (root / name).exists() else 0
                for name in (".effects", ".mounts", ".starts", ".stops")
            }

            return self.response(200, json.dumps(counts).encode(), "application/json")

        if parsed.path == "/__fixture__/edit":
            action = parse_qs(parsed.query).get("action", ["repair"])[0]

            if action == "break":
                (root / "component.py").write_text(
                    'STEP = 999\nraise RuntimeError("broken hot import")\n'
                )
            elif action == "bad_state":
                (root / "component.py").write_text(
                    "def step():\n    return 100\n"
                    'def label(count):\n    if count: raise ValueError("state render failed")\n'
                    '    return "bad"\n'
                )
            elif action == "css":
                (root / "style.css").write_text("color: rgb(0, 0, 255)")
            elif action == "template":
                (root / "note.txt").write_text("template two")
            elif action == "incompatible":
                (root / "user_app.py").write_text(HOT_SOURCE.replace("SCHEMA = 1", "SCHEMA = 2"))
            elif action == "original":
                hot_project(root)
            else:
                source = COMPONENT.replace("STEP = 1", "STEP = 2").replace(
                    '"original"', repr(action)
                )

                if action == "slow":
                    source = "import time\ntime.sleep(.4)\n" + source
                (root / "component.py").write_text(source)

            return self.response(200, b"edited")

        return await super().http(_, request)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    original = Path.cwd()

    with tempfile.TemporaryDirectory(prefix="pysx-hot-") as directory:
        root = Path(directory)
        hot_project(root)
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
