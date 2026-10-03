"""Headless acceptance for the todos port."""

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from websockets.asyncio.client import connect  # noqa: E402

PORT = 8753


async def recv(ws, seconds=5):
    return json.loads(await asyncio.wait_for(ws.recv(), seconds))


async def send(ws, handler, value=None):
    await emit(ws, handler, value)
    return await recv(ws)


async def emit(ws, handler, value=None):
    msg = {"t": "event", "h": handler}
    if value is not None:
        msg["v"] = value
    await ws.send(json.dumps(msg))


async def silent(ws, handler, value=None, seconds=0.35):
    """Send an event and assert the server sends nothing back."""
    await emit(ws, handler, value)
    try:
        frame = await asyncio.wait_for(ws.recv(), seconds)
    except (TimeoutError, asyncio.TimeoutError):
        return
    raise AssertionError(f"expected no frame, got {frame}")


async def idle(ws, seconds=0.35):
    out = []
    try:
        while True:
            out.append(json.loads(await asyncio.wait_for(ws.recv(), seconds)))
    except (TimeoutError, asyncio.TimeoutError):
        return out


def ops_of(patch, kind):
    return [o for o in patch["ops"] if o["op"] == kind]


async def run():
    async with connect(f"ws://127.0.0.1:{PORT}/ws") as a, \
               connect(f"ws://127.0.0.1:{PORT}/ws") as b:
        init = await recv(a)
        await recv(b)
        html = init["html"]
        assert html.count('data-pysx-key="') == 3, html
        assert '<pysx-slot id="2">2</pysx-slot>' in html, html
        assert '<pysx-slot id="3">s</pysx-slot>' in html, html
        print("  ok  3 todos render, '2 items left' via text + plural slots")

        handlers = re.findall(r'data-pysx-(?:click|change)="([^"]+)"', html)
        assert len(handlers) == len(set(handlers)), f"duplicate handler ids: {handlers}"
        print("  ok  every handler id in the document is unique")

        # toggle todo 1 -> only that item's html goes on the wire
        patch = await send(a, "h10:1:0")
        lists = ops_of(patch, "list")
        assert len(lists) == 1, patch
        assert list(lists[0]["html"]) == ["1"], lists[0]["html"]
        assert lists[0]["keys"] == ["1", "2", "3"], lists[0]
        assert "is-done" in lists[0]["html"]["1"]
        assert ops_of(patch, "text"), "remaining count should change"
        print("  ok  toggle sends html for exactly 1 of 3 items")

        # remove todo 2 (handler :1; :0 is its checkbox) -> order shrinks, zero html
        patch = await send(a, "h10:2:1")
        lists = ops_of(patch, "list")
        assert lists[0]["keys"] == ["1", "3"], lists[0]
        assert lists[0]["html"] == {}, "a removal must send no html"
        print("  ok  remove sends new key order and zero html")

        # Typing must produce no frame at all: the only watcher on draft is the
        # input's own value, and echoing it back would reset the caret.
        await silent(a, "h1", "Write the port")
        print("  ok  typing produces zero frames (no echo to the source input)")

        patch = await send(a, "h0")                     # submit
        lists = ops_of(patch, "list")
        assert lists[0]["keys"] == ["1", "3", "4"], lists[0]
        assert list(lists[0]["html"]) == ["4"], lists[0]["html"]
        assert "Write the port" in lists[0]["html"]["4"]
        cleared = [o for o in ops_of(patch, "attr") if o["name"] == "value"]
        assert cleared and cleared[0]["v"] == "", patch
        print("  ok  add: only the new item ships html, input cleared via attr op")

        # filters
        # state here: 1 done, 3 done, 4 active -> "Active" shows only 4, and
        # item 4 is already on the client, so no html travels.
        patch = await send(a, "h7")
        lists = ops_of(patch, "list")
        assert lists[0]["keys"] == ["4"], lists[0]
        assert lists[0]["html"] == {}, "already-rendered item must not resend html"
        attrs = [o for o in ops_of(patch, "attr") if o["name"] == "class"]
        assert len(attrs) == 2, attrs
        # Every class op must carry the scoped hash; sending only the modifier
        # would blank the attribute and strip the element's styling.
        assert all(o["v"].startswith("pysx-") for o in attrs), attrs
        assert any(o["v"].endswith(" is-active") for o in attrs), attrs
        assert any("is-active" not in o["v"] for o in attrs), attrs
        print("  ok  filter narrows the list, moves is-active, keeps scoped classes")

        # Clear completed removes 1 and 3, but the Active filter was already
        # showing only item 4 -> the visible list is unchanged and no list op
        # is sent at all. Only the "Clear completed" branch disappears.
        patch = await send(a, "h12")
        assert ops_of(patch, "list") == [], patch
        assert any(o["op"] == "html" and o["id"] == "11" and o["v"] == ""
                   for o in patch["ops"]), patch
        print("  ok  clear completed sends only the branch removal, no list op")

        leaked = await idle(b)
        assert leaked == [], f"session B saw session A's traffic: {leaked}"
        print("  ok  session B received zero frames throughout")

    async with connect(f"ws://127.0.0.1:{PORT}/ws") as c:
        init = await recv(c)
        assert init["html"].count('data-pysx-key="') == 3, "fresh session not isolated"
        print("  ok  a fresh session starts from the original 3 todos")


def main():
    proc = subprocess.Popen(
        [sys.executable, "-m", "pysx.server",
         "--app", "examples.todos:app", "--port", str(PORT)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        for line in proc.stdout:
            if "pysx ready" in line:
                break
        else:
            raise SystemExit("server did not start")
        asyncio.run(run())
        print("TODOS ACCEPTANCE PASSED")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
