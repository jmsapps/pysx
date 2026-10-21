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

from pysx.development.development import Supervisor, WatchConfig

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection
    from websockets.http11 import Request, Response


class Fixture(Supervisor):
    clients: set[ServerConnection]

    def __post_init__(self) -> None:
        super().__post_init__()
        self.clients = set()

    async def websocket(self, client: ServerConnection) -> None:
        self.clients.add(client)

        try:
            await super().websocket(client)
        finally:
            self.clients.remove(client)

    async def http(self, _: ServerConnection, request: Request) -> Response | None:
        parsed = urlsplit(request.path)

        if parsed.path == "/__fixture__/edit":
            action = parse_qs(parsed.query).get("action", ["repair"])[0]
            root = self.config.roots[0]

            if action == "disconnect":
                for client in tuple(self.clients):
                    if client.request is not None and not client.request.path.endswith(
                        "/__pysx_dev__/events"
                    ):
                        await client.close(1001, "test network interruption")

                return self.response(200, b"disconnected")
            source = (
                'raise RuntimeError("broken browser edit")\n'
                if action == "break"
                else (
                    "def label(count=0):\n    if count > 0:\n"
                    '        raise RuntimeError("restored state render failed")\n'
                    '    return "bad-state"\n'
                )
                if action == "bad_state"
                else f'def label(count=0):\n    return "{action}"\n'
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
        source = root / "user_app.py"
        source.write_text(
            source.read_text()
            .replace("label()", "label(count())")
            .replace(
                "    count = signal(0)", '    count = signal(0)\n    draft = signal("initial")'
            )
            .replace(
                '        native.P(count, id="count"),',
                """        native.P(count, id="count"),
        native.Input(id="field", bind_value=draft),
        native.P(draft, id="echo"),
        native.Input(id="uncommitted-check", type="checkbox"),
        native.Select(native.Option("A", value="a"), native.Option("B", value="b"),
            id="uncommitted-choice"),
        native.Button("New handler", id="handler", onClick=lambda _event: draft.set(label())),""",
            )
        )
        (root / "component.py").write_text('def label(count=0):\n    return "original"\n')
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
