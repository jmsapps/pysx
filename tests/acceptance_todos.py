"""Headless acceptance for the todos port."""

import asyncio
import json
import re
import sys
from typing import TYPE_CHECKING, Literal, overload

from acceptance_support import ready_server, receive
from websockets.asyncio.client import ClientConnection, connect

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pysx.wire import AttrOp, ListOp, Op, PatchMessage, ServerMessage

PORT = 8753


async def recv(ws: ClientConnection, seconds: float = 5) -> ServerMessage:
    return await receive(ws, seconds)


async def send(ws: ClientConnection, handler: str, value: str | bool | None = None) -> PatchMessage:
    await emit(ws, handler, value)
    message = await recv(ws)
    assert message["t"] == "patch"

    return message


async def emit(ws: ClientConnection, handler: str, value: str | bool | None = None) -> None:
    msg: dict[str, str | bool] = {"t": "event", "h": handler}

    if value is not None:
        msg["v"] = value
    await ws.send(json.dumps(msg))


async def silent(
    ws: ClientConnection,
    handler: str,
    value: str | bool | None = None,
    seconds: float = 0.35,
) -> None:
    """Send an event and assert the server sends nothing back."""
    await emit(ws, handler, value)

    try:
        frame = await asyncio.wait_for(ws.recv(), seconds)
    except TimeoutError:
        return

    raise AssertionError(f"expected no frame, got {frame!r}")


async def idle(ws: ClientConnection, seconds: float = 0.35) -> list[ServerMessage]:
    out: list[ServerMessage] = []

    try:
        while True:
            out.append(await recv(ws, seconds))
    except TimeoutError:
        return out


@overload
def ops_of(patch: PatchMessage, kind: Literal["list"]) -> list[ListOp]: ...


@overload
def ops_of(patch: PatchMessage, kind: Literal["attr"]) -> list[AttrOp]: ...


@overload
def ops_of(patch: PatchMessage, kind: str) -> Sequence[Op]: ...


def ops_of(patch: PatchMessage, kind: str) -> Sequence[Op]:
    return [o for o in patch["ops"] if o["op"] == kind]


async def run() -> None:
    async with connect(f"ws://127.0.0.1:{PORT}/ws") as a, connect(f"ws://127.0.0.1:{PORT}/ws") as b:
        init = await recv(a)
        await recv(b)
        assert init["t"] == "init"
        html = init["html"]
        assert html.count('data-pysx-key="') == 3, html
        assert '<pysx-slot id="2">2</pysx-slot>' in html, html
        assert '<pysx-slot id="3">s</pysx-slot>' in html, html
        print("  ok  3 todos render, '2 items left' via text + plural slots")

        handlers = re.findall(r'data-pysx-(?:click|change)="([^"]+)"', html)
        assert len(handlers) == len(set(handlers)), f"duplicate handler ids: {handlers}"
        print("  ok  every handler id in the document is unique")

        # Toggle patches the retained row's properties/classes and the summary.
        toggle = next(hid for hid in handlers if re.fullmatch(r"h10:1:g\d+:0", hid))
        patch = await send(a, toggle)
        lists = ops_of(patch, "list")
        assert lists == [], patch
        assert any(
            op["op"] == "attr" and op["name"] == "class" and "is-done" in (op["v"] or "")
            for op in patch["ops"]
        )
        assert ops_of(patch, "text"), "remaining count should change"
        print("  ok  toggle sends granular patches and zero row html")

        # remove todo 2 (handler :1; :0 is its checkbox) -> order shrinks, zero html
        remove = next(hid for hid in handlers if re.fullmatch(r"h10:2:g\d+:1", hid))
        patch = await send(a, remove)
        lists = ops_of(patch, "list")
        assert lists[0]["keys"] == ["1", "3"], lists[0]
        assert lists[0]["html"] == {}, "a removal must send no html"
        print("  ok  remove sends new key order and zero html")

        # Typing must produce no frame at all: the only watcher on draft is the
        # input's own value, and echoing it back would reset the caret.
        await silent(a, "h1", "Write the port")
        print("  ok  typing produces zero frames (no echo to the source input)")

        patch = await send(a, "h0")  # submit
        lists = ops_of(patch, "list")
        assert lists[0]["keys"] == ["1", "3", "4"], lists[0]
        assert list(lists[0]["html"]) == ["4"], lists[0]["html"]
        assert "Write the port" in lists[0]["html"]["4"]
        cleared = [o for o in ops_of(patch, "attr") if o["name"] == "value"]
        assert cleared, patch
        assert cleared[0]["v"] == "", patch
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
        assert all((o["v"] or "").startswith("pysx-") for o in attrs), attrs
        assert any((o["v"] or "").endswith(" is-active") for o in attrs), attrs
        assert any("is-active" not in (o["v"] or "") for o in attrs), attrs
        print("  ok  filter narrows the list, moves is-active, keeps scoped classes")

        # Clear completed removes 1 and 3, but the Active filter was already
        # showing only item 4 -> the visible list is unchanged and no list op
        # is sent at all. Only the "Clear completed" branch disappears.
        patch = await send(a, "h12")
        assert ops_of(patch, "list") == [], patch
        assert any(o["op"] == "html" and o["id"] == "11" and o["v"] == "" for o in patch["ops"]), (
            patch
        )
        print("  ok  clear completed sends only the branch removal, no list op")

        leaked = await idle(b)
        assert leaked == [], f"session B saw session A's traffic: {leaked}"
        print("  ok  session B received zero frames throughout")

    async with connect(f"ws://127.0.0.1:{PORT}/ws") as c:
        init = await recv(c)
        assert init["t"] == "init"
        assert init["html"].count('data-pysx-key="') == 3, "fresh session not isolated"
        print("  ok  a fresh session starts from the original 3 todos")


def main() -> None:
    with ready_server(
        [sys.executable, "-m", "pysx.server", "--app", "examples.todos:app", "--port", str(PORT)],
    ):
        asyncio.run(run())
        print("TODOS ACCEPTANCE PASSED")


if __name__ == "__main__":
    main()
