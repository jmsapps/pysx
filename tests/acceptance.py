"""Headless definition of done (PLAN.md §1).

Starts the real server, drives it over two WebSocket connections, and asserts
both the patch-economy and session-isolation properties.
"""

import asyncio
import json
import sys
import urllib.request
from typing import TYPE_CHECKING

from acceptance_support import ready_server, receive
from websockets.asyncio.client import ClientConnection, connect

if TYPE_CHECKING:
    from pysx.wire import InitMessage, PatchMessage, ServerMessage

PORT = 8751  # not 8750, so a dev server can stay up while this runs
BASE = f"http://127.0.0.1:{PORT}"


async def _init(ws: ClientConnection) -> InitMessage:
    message = await receive(ws)
    assert message["t"] == "init", message
    return message


async def _click(ws: ClientConnection, handler_id: str = "h1") -> PatchMessage:
    await ws.send(json.dumps({"t": "event", "h": handler_id}))
    message = await receive(ws)
    assert message["t"] == "patch"
    return message


async def _drain(ws: ClientConnection, seconds: float = 0.4) -> list[ServerMessage]:
    """Collect whatever arrives in a window; used to prove nothing arrives."""
    out: list[ServerMessage] = []
    try:
        while True:
            out.append(await receive(ws, seconds))
    except TimeoutError:
        return out


async def run() -> None:
    async with connect(f"ws://127.0.0.1:{PORT}/ws") as a, \
               connect(f"ws://127.0.0.1:{PORT}/ws") as b:

        init_a = await _init(a)
        await _init(b)
        assert "<button" in init_a["html"], init_a["html"]
        assert 'data-pysx-click="h1"' in init_a["html"], init_a["html"]
        assert '<pysx-slot id="0">0</pysx-slot>' in init_a["html"], init_a["html"]
        assert ".pysx-" in init_a["css"], init_a["css"]
        print("  ok  init frame carries html + css, slot prefilled at 0")

        patches = [await _click(a) for _ in range(3)]
        assert [p["t"] for p in patches] == ["patch"] * 3, patches
        assert [p["ops"] for p in patches] == [
            [{"op": "text", "id": "0", "v": "1"}],
            [{"op": "text", "id": "0", "v": "2"}],
            [{"op": "text", "id": "0", "v": "3"}],
        ], patches
        for p in patches:
            assert set(p) == {"t", "ops"}, p
            assert "html" not in json.dumps(p), p
        print("  ok  3 events -> exactly 3 patch frames, one text op each, no HTML")

        leaked = await _drain(b)
        assert leaked == [], f"session B received traffic from A: {leaked}"
        print("  ok  session B received zero frames (isolation)")

    async with connect(f"ws://127.0.0.1:{PORT}/ws") as c:
        init_c = await _init(c)
        assert '<pysx-slot id="0">0</pysx-slot>' in init_c["html"], init_c["html"]
        print("  ok  a fresh session initialises at 0")


def main() -> None:
    with ready_server(
        [sys.executable, "-m", "pysx.server",
         "--app", "examples.counter:app", "--port", str(PORT)],
            ):

        with urllib.request.urlopen(BASE + "/", timeout=5) as r:
            assert r.status == 200, r.status
            assert r.headers["Content-Type"] == "text/html; charset=utf-8", \
                dict(r.headers)
            body = r.read().decode()
            assert "<!doctype html>" in body.lower(), body[:200]
        print("  ok  GET / -> 200 text/pysx (not text/plain)")

        with urllib.request.urlopen(BASE + "/client.js", timeout=5) as r:
            assert r.status == 200
            assert (r.headers["Content-Type"] or "").startswith("text/javascript"), \
                dict(r.headers)
        print("  ok  GET /client.js -> 200 text/javascript")

        asyncio.run(run())
        print("ACCEPTANCE PASSED")


if __name__ == "__main__":
    main()
