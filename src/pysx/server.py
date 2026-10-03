"""HTTP + WebSocket on one port.

One WebSocket connection is one session: the component body runs once per
connection, so its signals are per-client by construction.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from http import HTTPStatus
from pathlib import Path

from websockets.asyncio.server import serve

from .reactive import Effect
from .render import render

STATIC = Path(__file__).parent / "static"


class Session:
    """Holds one client's signal graph and the ops it has produced."""

    def __init__(self, app_fn) -> None:
        self.pending: list[dict] = []
        self._live = False
        self.rendered = render(app_fn)
        # The first run of each effect registers its subscription; watchers were
        # seeded with the rendered value, so it produces no ops.
        self.effects = [
            Effect(lambda w=w: self._collect(w)) for w in self.rendered.watchers
        ]
        self._live = True

    def _collect(self, watcher) -> None:
        ops = watcher.refresh()
        if self._live and ops:
            self.pending.extend(ops)

    def dispatch(self, handler_id: str, value: object) -> list[dict]:
        fn = self.rendered.handlers.get(handler_id)
        if fn is None:
            return []
        self.pending.clear()
        fn(value)
        ops = self.pending
        self.pending = []
        origin = self.rendered.bind_elements.get(handler_id)
        if origin is not None:
            # Do not echo a value back to the element that just produced it.
            ops = [o for o in ops if not (o["op"] == "attr" and o["id"] == origin)]
        return ops

    def dispose(self) -> None:
        for eff in self.effects:
            eff.dispose()


def _static(connection, name: str, content_type: str):
    response = connection.respond(HTTPStatus.OK, (STATIC / name).read_text("utf-8"))
    # respond() hardcodes text/plain, and assigning a header appends rather
    # than replaces, so the original must be removed first.
    del response.headers["Content-Type"]
    response.headers["Content-Type"] = content_type
    return response


def _load_app(spec: str):
    module_name, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module_name), attr or "app")


def main() -> None:
    ap = argparse.ArgumentParser(prog="pysx.server")
    ap.add_argument("--app", required=True, help="module:attr of the @component to serve")
    ap.add_argument("--port", type=int, default=8750)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    app_fn = _load_app(args.app)

    async def process_request(connection, request):
        if request.path in ("/", "/index.html"):
            return _static(connection, "index.html", "text/html; charset=utf-8")
        if request.path == "/client.js":
            return _static(connection, "client.js", "text/javascript; charset=utf-8")
        if request.path == "/ws":
            return None
        return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")

    async def handler(connection):
        session = Session(app_fn)
        try:
            await connection.send(json.dumps({
                "t": "init",
                "html": session.rendered.body,
                "css": session.rendered.css,
            }))
            async for raw in connection:
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if message.get("t") != "event":
                    continue
                ops = session.dispatch(message.get("h", ""), message.get("v"))
                if ops:
                    await connection.send(json.dumps({"t": "patch", "ops": ops}))
        finally:
            session.dispose()

    async def run() -> None:
        async with serve(handler, args.host, args.port, process_request=process_request):
            print(f"pysx ready -> http://{args.host}:{args.port}", flush=True)
            await asyncio.get_running_loop().create_future()

    try:
        asyncio.run(run())
    except OSError as exc:
        raise SystemExit(f"cannot bind {args.host}:{args.port}: {exc}") from exc
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
