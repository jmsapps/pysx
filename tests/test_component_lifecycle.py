"""Component identity, teardown and browser acknowledgement contracts."""

import asyncio
import json
import re
import sys
from dataclasses import asdict
from typing import TYPE_CHECKING, cast

import pytest
from acceptance_support import ready_server, receive
from websockets.asyncio.client import connect

from pysx import (
    BrowserEvent,
    Fragment,
    Signal,
    each,
    local_state,
    on_cleanup,
    on_mount,
    on_setup,
    own_effect,
    own_subscription,
    own_task,
    own_timer,
    pysx,
    signal,
)
from pysx.render import ListWatcher
from pysx.server import Session, close_session

if TYPE_CHECKING:
    from collections.abc import Callable
    from string.templatelib import Template


def test_stable_component_reorder_removal_and_remount() -> None:
    rows = signal(["a", "b"])
    source = signal(0)
    setup: list[str] = []
    cleanup: list[str] = []
    counts: dict[str, Signal[int]] = {}

    def row(key: str) -> Fragment:
        count = local_state("count", 0)
        counts[key] = count
        on_setup(lambda: setup.append(key))
        on_cleanup(lambda: cleanup.append(key))
        own_effect("observe", lambda: source())

        def increment(_: object) -> None:
            count.set(count() + 1)

        return pysx(t"\nli:\n  button(onClick={increment}): {count}")

    def app() -> Fragment:
        return pysx(t"\nul: {each(rows, row, key=str)}")

    session = Session(app)
    first = counts["a"]
    session.dispatch("h0:a:0", None)
    rows.set(["b", "a"])
    assert counts["a"] is first
    assert setup == ["a", "b"]
    assert len(source.observers) == 2
    watcher = session.rendered.watchers[0]
    assert isinstance(watcher, ListWatcher)
    assert "1" in watcher.markup["a"]
    rows.set(["b"])
    assert cleanup == ["a"]
    assert len(source.observers) == 1
    rows.set(["a", "b"])
    assert counts["a"] is not first
    assert setup == ["a", "b", "a"]
    session.dispose()
    session.dispose()
    assert sorted(cleanup) == ["a", "a", "b"]
    assert rows.observers == {}
    assert source.observers == {}


def test_stable_component_identity_follows_definitions_not_objects() -> None:
    rows = signal(["a", "b"])
    setups: list[str] = []
    states: dict[str, Signal[int]] = {}

    class Card:
        def __call__(self, *, label: str) -> Template:
            states[f"card:{label}"] = local_state("count", 0)
            on_setup(lambda: setups.append(f"card:{label}"))

            return t"\nspan: {states[f'card:{label}']}"

    class Panel:
        def view(self, *, label: str) -> Template:
            states[f"panel:{label}"] = local_state("count", 0)
            on_setup(lambda: setups.append(f"panel:{label}"))

            return t"\nspan: {states[f'panel:{label}']}"

    def row(key: str) -> Fragment:
        return pysx(
            t"\nli:\n  Card(label={key}):\n  Panel(label={key}):",
            namespace={"Card": Card(), "Panel": Panel().view},
        )

    def app() -> Fragment:
        return pysx(t"\nul: {each(rows, row, key=str)}")

    session = Session(app)
    card, panel = states["card:a"], states["panel:a"]
    rows.set(["b", "a"])
    rows.set(["a", "b"])
    assert setups == ["card:a", "panel:a", "card:b", "panel:b"]
    assert states["card:a"] is card
    assert states["panel:a"] is panel
    session.dispose()


def test_stable_component_branch_mount_ack_and_cleanup_errors() -> None:
    visible = signal(True)
    events: list[str] = []

    def child() -> Template:
        on_setup(lambda: events.append("setup"))
        on_mount(lambda: events.append("mount"))
        on_cleanup(lambda: events.append("cleanup"))

        return t'\nspan: "child"'

    def app() -> Fragment:
        return pysx(t"\nif {visible}:\n  Child:", namespace={"Child": child})

    session = Session(app)
    tokens = session.rendered.scopes.pending_mounts()
    visible.set(False)
    session.rendered.scopes.acknowledge(tokens)
    assert events == ["setup", "cleanup"]
    visible.set(True)
    current = session.rendered.scopes.pending_mounts()
    assert current != tokens
    session.rendered.scopes.acknowledge(current)
    session.rendered.scopes.acknowledge(current)
    assert events == ["setup", "cleanup", "setup", "mount"]
    session.dispose()
    assert events[-1] == "cleanup"


def test_stable_component_failed_setup_releases_all_resources() -> None:
    events: list[str] = []
    source = signal(0)

    def fail_cleanup() -> None:
        events.append("throw")

        raise ValueError("cleanup failure")

    def app() -> Template:
        own_effect("observe", lambda: source())
        on_cleanup(lambda: events.append("clean"))
        on_cleanup(fail_cleanup)

        raise RuntimeError("setup failure")

    with pytest.raises(RuntimeError, match="setup failure"):
        Session(app)
    assert events == ["throw", "clean"]
    assert source.observers == {}
    source.set(1)


def test_stable_component_cleanup_continues_after_failure() -> None:
    events: list[str] = []

    def bad() -> None:
        events.append("bad")

        raise ValueError("bad cleanup")

    def app() -> Template:
        on_cleanup(lambda: events.append("good"))
        on_cleanup(bad)

        return t'\nspan: "ready"'

    session = Session(app)

    with pytest.raises(ExceptionGroup, match="session cleanup"):
        session.dispose()
    session.dispose()
    assert events == ["bad", "good"]
    assert session.rendered.scopes.owners == {}


def test_stable_component_nested_branches_keys_and_watchers() -> None:
    rows = signal(["a", "a:c1"])
    nested = signal(["one"])
    states: dict[str, Signal[int]] = {}
    setups: list[str] = []

    def child(*, label: str) -> Template:
        count = local_state("count", 0)
        states[label] = count
        on_setup(lambda: setups.append(label))

        return t"\nspan: {count}"

    def leaf(value: str) -> Fragment:
        return pysx(t"\nli: {value}")

    def row(key: str) -> Fragment:
        return pysx(
            t"\nli:\n  if {True}:\n    Child(label={key}):\n  ul: {each(nested, leaf, key=str)}",
            namespace={"Child": child},
        )

    def app() -> Fragment:
        return pysx(t"\nul: {each(rows, row, key=str)}")

    session = Session(app)
    first = states["a"]
    assert first is not states["a:c1"]
    first.set(3)

    for _ in range(3):
        rows.set(list(reversed(rows())))
    assert states["a"] is first
    assert setups == ["a", "a:c1"]
    assert len(session.rendered.watchers) == 1
    assert len(nested.observers) == 1
    session.dispose()
    assert nested.observers == {}


def test_stable_component_mount_can_own_cleanup() -> None:
    events: list[str] = []

    def mounted() -> None:
        events.append("mounted")
        on_cleanup(lambda: events.append("unmounted"))

    def app() -> Template:
        on_mount(mounted)

        return t'\nspan: "ready"'

    session = Session(app)
    session.rendered.scopes.acknowledge(session.rendered.scopes.pending_mounts())
    session.dispose()
    assert events == ["mounted", "unmounted"]


def test_composition_recursive_dispatch_isolation_and_owned_cleanup() -> None:
    from examples.components.composition import tree_controls

    session, other = Session(tree_controls), Session(tree_controls)
    initial = len(session.rendered.scopes.owners)
    match = re.search(r'id="tree-library"[^>]*data-pysx-click="([^"]+)"', session.rendered.body)
    assert match is not None
    handler = match[1]

    try:
        opened = session.dispatch(handler, None, event=asdict(BrowserEvent("click", handler)))
        assert any(
            op["op"] == "list"
            and any('id="tree-components"' in html for html in op["html"].values())
            for op in opened
        )
        assert len(session.rendered.scopes.owners) > initial
        assert len(session.rendered.dom.mounts) == 1
        assert other.pending == []
        assert len(other.rendered.scopes.owners) == initial
        session.dispatch(handler, None, event=asdict(BrowserEvent("click", handler)))
        assert len(session.rendered.scopes.owners) == initial
    finally:
        session.dispose()
        other.dispose()
    assert session.rendered.scopes.owners == {}
    assert session.rendered.handlers == {}


def test_stable_component_setup_write_corrects_an_earlier_hole() -> None:
    ready = signal(0)

    def child() -> Template:
        on_setup(lambda: ready.set(2))

        return t'\nspan: "child"'

    def app() -> Fragment:
        return pysx(t"\np: {ready}\nChild:", namespace={"Child": child})

    session = Session(app)

    try:
        assert '<pysx-slot id="0">0</pysx-slot>' in session.rendered.body
        assert session.pending == [{"op": "text", "id": "0", "v": "2"}]
    finally:
        session.dispose()


def test_stable_component_connection_cleanup_keeps_the_original_error() -> None:
    def fail() -> None:
        raise ValueError("bad cleanup")

    def app() -> Template:
        on_cleanup(fail)

        return t'\nspan: "ready"'

    async def exercise() -> None:
        failure = RuntimeError("connection failed")
        await close_session(Session(app), None, failure)
        assert any("session cleanup also failed" in note for note in failure.__notes__)

        with pytest.raises(ExceptionGroup, match="session cleanup"):
            await close_session(Session(app), None, None)

    asyncio.run(exercise())


@pytest.mark.acceptance
def test_stable_component_setup_patch_and_mount_follow_a_failed_event() -> None:
    port = 8761
    command = [
        sys.executable, "-m", "pysx.server",
        "--app", "tests.mount_protocol_fixture:app", "--port", str(port),
    ]

    async def exercise() -> None:
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            initial = await receive(ws)
            assert initial["t"] == "init"
            assert '<pysx-slot id="0">pending</pysx-slot>' in initial["html"]
            corrected = await receive(ws)
            assert corrected["t"] == "patch"
            assert corrected["ops"] == [{"op": "text", "id": "0", "v": "ready"}]
            found = re.search(r'id="reveal"[^>]*data-pysx-click="([^"]+)"', initial["html"])
            assert found is not None
            await ws.send(json.dumps({"t": "event", "h": found[1]}))
            patch = await receive(ws)
            assert patch["t"] == "patch"
            assert [op["op"] for op in patch["ops"]] == ["list"]
            mount = await receive(ws)
            assert mount["t"] == "mount"
            assert mount["ids"]
            await ws.send(json.dumps({"t": "mounted", "ids": mount["ids"]}))
            acknowledged = await receive(ws)
            assert acknowledged["t"] == "patch"
            assert acknowledged["ops"] == [{"op": "text", "id": "1", "v": "1"}]

    with ready_server(command):
        asyncio.run(exercise())


def test_stable_component_owned_resources_and_late_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        events: list[str] = []
        gate = asyncio.Event()
        started = asyncio.Event()
        source = signal(0)

        async def worker() -> object:
            started.set()

            try:
                await gate.wait()
                events.append("task wrote")
            finally:
                events.append("task cancelled")

            return None

        def subscribe() -> Callable[[], object]:
            events.append("subscribed")

            return lambda: events.append("unsubscribed")

        def app() -> Template:
            state = local_state("count", 0)
            own_effect("observe", lambda: source())
            own_subscription("external", subscribe)
            own_timer("clock", 0.02, lambda: events.append("timer wrote"))
            own_task("worker", worker)
            on_mount(lambda: state.set(1))

            return t"\nspan: {state}"

        scheduled: list[Callable[[], object]] = []
        cancelled: list[bool] = []

        class Handle:
            def cancel(self) -> None:
                cancelled.append(True)

        def call_later(delay: float, callback: Callable[[], object]) -> Handle:
            assert delay == 0.02
            scheduled.append(callback)

            return Handle()

        with monkeypatch.context() as patch:
            patch.setattr(asyncio.get_running_loop(), "call_later", call_later)
            session = Session(app)
        await started.wait()
        owner = session.rendered.scopes.owners[""]
        state = owner.values["count"]
        tokens = session.rendered.scopes.pending_mounts()
        session.dispose()
        session.rendered.scopes.acknowledge(tokens)
        gate.set()
        scheduled[0]()
        await asyncio.sleep(0)
        assert cancelled == [True]
        assert events == ["subscribed", "unsubscribed", "task cancelled"]
        assert source.observers == {}

        assert isinstance(state, Signal)

        with pytest.raises(RuntimeError, match="disposed"):
            cast("Signal[int]", state).set(2)

    asyncio.run(exercise())
