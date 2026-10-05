"""Owned command ordering, asynchronous replies and deterministic cancellation."""

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING, cast

import pytest

from pysx import (
    BrowserEvent,
    Dom,
    DomError,
    DomNode,
    DomRef,
    Fragment,
    html,
    native,
    on_event,
    signal,
)
from pysx.server import Session

if TYPE_CHECKING:
    from pysx.wire import JSONValue


def make_session() -> tuple[Session, DomRef]:
    refs: list[DomRef] = []

    def app() -> Fragment:
        ref = Dom().ref(imperative=True)
        refs.append(ref)

        return native.Div(ref=ref)

    return Session(app), refs[0]


def test_owned_dom_async_read_and_reply_version() -> None:
    async def check() -> None:
        session, ref = make_session()
        controller = session.rendered.dom
        sent: list[dict[str, JSONValue]] = []

        async def sender(message: dict[str, JSONValue]) -> None:
            sent.append(message)

        controller.sender = sender
        request = asyncio.create_task(ref.handle().get_attribute("title"))
        await asyncio.sleep(0)
        assert not request.done()
        controller.reply({"id": sent[0]["id"], "version": 2, "value": "wrong"})
        assert not request.done()
        controller.reply({"id": sent[0]["id"], "version": 1, "value": "ready"})
        assert await request == "ready"
        assert not controller.pending
        session.dispose()

    asyncio.run(check())


@pytest.mark.parametrize(
    "reply", [{"value": "x" * 16385}, {"value": float("nan")}, {"error": "removed"}, {}]
)
def test_owned_dom_reject_bounded_replies(reply: dict[str, object]) -> None:
    async def check() -> None:
        session, ref = make_session()
        controller = session.rendered.dom

        async def sender(message: dict[str, JSONValue]) -> None:
            controller.reply({"id": message["id"], "version": 1, **reply})

        controller.sender = sender
        with pytest.raises(DomError):
            await ref.handle().get_attribute("title")
        assert not controller.pending
        session.dispose()

    asyncio.run(check())


def test_owned_dom_disconnect_cancels_requests() -> None:
    async def check() -> None:
        session, ref = make_session()

        async def sender(_message: dict[str, JSONValue]) -> None:
            pass

        session.rendered.dom.sender = sender
        pending = asyncio.create_task(ref.handle().measure())
        await asyncio.sleep(0)
        session.dispose()
        with pytest.raises(DomError, match="owner removed"):
            await pending
        assert not session.rendered.dom.pending
        assert not session.rendered.dom.nodes

    asyncio.run(check())


def test_owned_dom_replacement_rejects_old_handle() -> None:
    async def check() -> None:
        visible = signal(True)
        refs: list[DomRef] = []

        def app() -> Fragment:
            ref = Dom().ref()
            refs.append(ref)

            return html(t"""
                if {visible}:
                    input(ref={ref})
            """)

        session = Session(app)
        old = refs[0].handle()
        visible.set(False)
        visible.set(True)
        assert refs[0].handle().token != old.token
        with pytest.raises(DomError, match="stale"):
            await old.focus()
        session.dispose()

    asyncio.run(check())


def test_owned_dom_cross_owner_insertion_and_command_limits() -> None:
    async def check() -> None:
        session, ref = make_session()
        node = ref.handle()
        with pytest.raises(DomError, match="across owners"):
            await node.append(DomNode(node.controller, "foreign", "foreign"))

        async def sender(_message: dict[str, JSONValue]) -> None:
            pass

        node.controller.sender = sender
        with pytest.raises(DomError, match="exceed"):
            await node.set_attribute("title", "x" * 8193)
        session.dispose()

    asyncio.run(check())


def test_owned_dom_async_dispatch() -> None:
    async def check() -> None:
        value = signal("initial")

        async def callback(_event: object) -> None:
            await asyncio.sleep(0)
            value.set("settled")

        def app() -> Fragment:
            return native.Div(native.Button("go", on_click=callback), native.P(value))

        session = Session(app)
        ops = await session.dispatch_async(next(iter(session.rendered.handlers)), None)
        assert any(op["op"] == "text" and op["v"] == "settled" for op in ops)
        session.dispose()

    asyncio.run(check())


def test_owned_dom_listener_registration_cleanup() -> None:
    async def check() -> None:
        session, ref = make_session()
        controller = session.rendered.dom

        async def sender(message: dict[str, JSONValue]) -> None:
            controller.reply({"id": message["id"], "version": 1, "value": None})

        controller.sender = sender
        listener = await ref.handle().listen("click", on_event(lambda _event: None))
        assert len(controller.listeners) == 1
        await listener.close()
        assert not controller.listeners
        session.dispose()

    asyncio.run(check())


def test_owned_dom_listener_honours_a_custom_event_type() -> None:
    async def check() -> None:
        session, ref = make_session()
        controller = session.rendered.dom
        sent: list[dict[str, JSONValue]] = []
        received: list[BrowserEvent] = []

        async def sender(message: dict[str, JSONValue]) -> None:
            sent.append(message)
            controller.reply({"id": message["id"], "version": 1, "value": None})

        controller.sender = sender
        handler = on_event(received.append, event_type="custom-ready")
        await ref.handle().listen("click", handler)
        hid = next(iter(controller.listeners))
        assert controller.listeners[hid][1] == "custom-ready"
        policy = cast("dict[str, dict[str, object]]", sent[0]["args"])["policy"]
        assert policy["type"] == "custom-ready"
        snapshot = asdict(BrowserEvent("custom-ready", hid))
        session.dispatch(hid, None, event=snapshot)
        assert [event.type for event in received] == ["custom-ready"]
        session.dispose()

    asyncio.run(check())


def test_owned_dom_deadline_cleans_pending() -> None:
    async def check() -> None:
        session, ref = make_session()

        async def sender(_message: dict[str, JSONValue]) -> None:
            pass

        session.rendered.dom.sender = sender
        with pytest.raises(DomError, match="deadline"):
            await ref.handle().measure()
        assert not session.rendered.dom.pending
        session.dispose()

    asyncio.run(check())


def test_accessible_keyboard_owner_removal_cancels_reply_and_listener() -> None:
    async def check() -> None:
        session, ref = make_session()
        controller = session.rendered.dom
        sent: list[dict[str, JSONValue]] = []

        async def sender(message: dict[str, JSONValue]) -> None:
            sent.append(message)

            if message["op"] == "listen":
                controller.reply({"id": message["id"], "version": 1, "value": None})

        controller.sender = sender
        await ref.handle().listen("keydown", on_event(lambda _event: None))
        pending = asyncio.create_task(ref.handle().focus())
        await asyncio.sleep(0)
        controller.revoke("")
        with pytest.raises(DomError, match="removed"):
            await pending
        controller.reply({"id": sent[-1]["id"], "version": 1, "value": None})
        assert not controller.pending
        assert not controller.listeners
        session.dispose()

    asyncio.run(check())


def test_owned_dom_failed_async_callback_discards_following_callbacks() -> None:
    async def check() -> None:
        value = signal("initial")
        called: list[str] = []

        async def first(_value: object) -> None:
            value.set("changed")

            raise DomError("failed")

        async def following(_value: object) -> None:
            called.append("following")

        def app() -> Fragment:
            return native.Div(
                native.Button("first", on_click=first),
                native.Button("following", on_click=following),
                native.P(value),
            )

        session = Session(app)
        ids = list(session.rendered.handlers)
        with pytest.raises(DomError, match="failed"):
            await session.dispatch_async(ids[0], None, after=ids[1])
        assert called == []
        assert any(op["op"] == "text" and op["v"] == "changed" for op in session.pending)
        await session.dispatch_async(ids[1], None)
        assert called == ["following"]
        session.dispose()

    asyncio.run(check())
