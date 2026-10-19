"""Staged hot replacement uses declared state and never activates probe resources."""

from __future__ import annotations

import asyncio
import json
import socket
from typing import TYPE_CHECKING, cast

import pytest
from websockets.asyncio.client import connect

from pysx import (
    DurableApplication,
    HostConfig,
    SQLiteOwnershipStore,
    StateCodec,
    native,
    own_effect,
    own_task,
    resource,
)
from pysx.development_control import Control, control_server
from pysx.hosting import request_from_headers

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pysx import Fragment, Signal
    from pysx.durable import DurableSession
    from pysx.wire import JSONValue

HOT_SOURCE = """from pathlib import Path
from uuid import uuid4
import asyncio
import os
from pysx import (DurableApplication, StateCodec, SQLiteOwnershipStore, native,
    derived, project, own_task, own_effect, on_mount)
from component import label, step

SCHEMA = 1
MIGRATIONS = {}

def mark(filename):
    with Path(filename).open("a") as output:
        output.write(str(os.getpid()) + "\\n")

def view(state):
    generation = uuid4().hex
    count = derived(lambda: state()["count"])
    draft = project(state, lambda value: value["draft"],
        lambda value, text: {**value, "draft": text})
    own_effect("effect", lambda: mark(".effects"))
    on_mount(lambda: mark(".mounts"))

    async def background():
        mark(".starts")
        try:
            await asyncio.Event().wait()
        finally:
            mark(".stops")

    own_task("background", background)

    def increment(_event):
        state.set({**state(), "count": state()["count"] + step()})

    return native.Main(
        native.H1(label(state()["count"]), id="label"),
        native.Input(id="field", bind_value=draft),
        native.Input(id="uncommitted-check", type="checkbox"),
        native.Select(native.Option("A", value="a"), native.Option("B", value="b"),
            id="uncommitted-choice"),
        native.P(draft, id="echo"),
        native.P(count, id="count"),
        native.P(generation, id="generation"),
        native.P(Path("note.txt").read_text(), id="note"),
        native.Button("Increment", id="increment", onClick=increment),
        style=Path("style.css").read_text(),
    )

app = DurableApplication(view, initial=lambda: {"count": 0, "draft": "initial"},
    codec=StateCodec(lambda value: value, lambda value: value),
    store=SQLiteOwnershipStore(Path(".state.sqlite")), application="development",
    schema=SCHEMA, migrations=MIGRATIONS)
"""
COMPONENT = """STEP = 1
def step():
    return STEP
def label(_count):
    return "original"
"""


def hot_project(root: Path) -> None:
    (root / "user_app.py").write_text(HOT_SOURCE)
    (root / "component.py").write_text(COMPONENT)
    (root / "style.css").write_text("color: rgb(255, 0, 0)")
    (root / "note.txt").write_text("template one")


def integer(value: JSONValue) -> int:
    if type(value) is not int:
        raise ValueError("expected integer")

    return value


def test_state_preserving_staged_resources_and_live_state(tmp_path: Path) -> None:
    async def run() -> None:
        markers: list[str] = []

        async def load() -> str:
            markers.append("load")

            return "loaded"

        async def background() -> None:
            markers.append("task")

        def view(state: Signal[int]) -> Fragment:
            own_effect("effect", lambda: markers.append("effect"))
            own_task("task", background)
            resource("load", load)

            return native.P(state)

        app = DurableApplication(
            view,
            initial=lambda: 0,
            codec=StateCodec(lambda value: value, integer),
            store=SQLiteOwnershipStore(tmp_path / "state.db"),
            application="test",
            config=HostConfig(development=True),
        )
        control = Control(app)
        assert await control.validate([]) == "hot"
        await asyncio.sleep(0)
        assert markers == []
        request = request_from_headers("/", [("Cookie", "pysx_browser=" + "b" * 32)])
        graph = cast("DurableSession[int]", app.runtime.create_reserved(request, browser="b" * 32))
        await graph.runtime.update(lambda: graph.state.set(9))
        await asyncio.sleep(0.02)
        identity = graph.snapshot.identity
        row: JSONValue = {
            "identity": [
                identity.application,
                identity.session,
                identity.principal,
                identity.browser,
            ],
            "schema": 1,
            "payload": "9",
            "path": "/",
            "headers": dict(request.headers),
            "principal": None,
            "location": "/",
        }
        before = list(markers)
        assert await control.validate([row]) == "hot"
        await asyncio.sleep(0)
        assert markers == before
        assert graph.state() == 9
        await app.close()

    asyncio.run(run())


def test_state_preserving_control_disconnect_thaws_mutations(tmp_path: Path) -> None:
    async def run() -> None:
        app = DurableApplication(
            lambda state: native.P(state),
            initial=lambda: 0,
            codec=StateCodec(lambda value: value, integer),
            store=SQLiteOwnershipStore(tmp_path / "state.db"),
            application="test",
            config=HostConfig(development=True),
        )
        request = request_from_headers("/", [("Cookie", "pysx_browser=" + "b" * 32)])
        graph = cast("DurableSession[int]", app.runtime.create_reserved(request, browser="b" * 32))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        token = "t" * 40

        async with control_server(app, port, token):
            async with connect(f"ws://127.0.0.1:{port}", proxy=None) as denied:
                await denied.wait_closed()
                assert denied.close_code == 1008
            async with connect(
                f"ws://127.0.0.1:{port}",
                proxy=None,
                additional_headers={"Authorization": "Bearer " + token},
            ) as frozen:
                await frozen.send(json.dumps({"op": "freeze"}))
                reply = json.loads(await frozen.recv())
                assert len(reply["result"]) == 1
                updating = asyncio.create_task(graph.runtime.update(lambda: graph.state.set(4)))
                await asyncio.sleep(0.02)
                assert not updating.done()
            async with asyncio.timeout(2):
                await updating
            assert graph.state() == 4
        await app.close()

    asyncio.run(run())


@pytest.mark.parametrize(("schema", "migrate"), [(1, False), (2, False), (2, True)])
def test_state_preserving_migration_and_fallback(
    tmp_path: Path, schema: int, *, migrate: bool
) -> None:
    async def run() -> None:
        seen: list[int] = []
        calls: list[JSONValue] = []

        def upgrade(value: JSONValue) -> JSONValue:
            calls.append(value)

            return integer(value) + 1

        def view(state: Signal[int]) -> Fragment:
            seen.append(state())

            return native.P(state)

        app = DurableApplication(
            view,
            initial=lambda: 0,
            codec=StateCodec(lambda value: value, integer),
            store=SQLiteOwnershipStore(tmp_path / "state.db"),
            application="test",
            schema=schema,
            migrations={1: upgrade} if migrate else {},
            config=HostConfig(development=True),
        )
        row: JSONValue = {
            "identity": ["test", "a" * 40, "principal", "b" * 32],
            "schema": 1,
            "payload": "5",
            "path": "/",
            "headers": {},
            "principal": None,
            "location": "/",
        }
        assert await Control(app).validate([row]) == ("hot" if schema == 1 or migrate else "fresh")
        assert seen == ([6] if migrate else [5] if schema == 1 else [0])
        assert len(calls) == (1 if migrate else 0)
        await app.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", [ValueError, RuntimeError])
def test_state_preserving_render_failure_is_not_schema_fallback(
    tmp_path: Path,
    failure: type[Exception],
) -> None:
    async def run() -> None:
        def view(state: Signal[int]) -> Fragment:
            if state() == 5:
                raise failure("state-dependent render failure")

            return native.P(state)

        app = DurableApplication(
            view,
            initial=lambda: 0,
            codec=StateCodec(lambda value: value, integer),
            store=SQLiteOwnershipStore(tmp_path / "state.db"),
            application="test",
            config=HostConfig(development=True),
        )
        row: JSONValue = {
            "identity": ["test", "a" * 40, "principal", "b" * 32],
            "schema": 1,
            "payload": "5",
            "path": "/",
            "headers": {},
            "principal": None,
            "location": "/",
        }

        with pytest.raises(failure, match="state-dependent render failure"):
            await Control(app).validate([row])
        assert app.runtime.graphs == set()
        await app.close()

    asyncio.run(run())


def test_state_preserving_failed_migration_rejects_candidate(tmp_path: Path) -> None:
    async def run() -> None:
        def failed(_value: JSONValue) -> JSONValue:
            raise ValueError("bad migration")

        app = DurableApplication(
            lambda state: native.P(state),
            initial=lambda: 0,
            codec=StateCodec(lambda value: value, integer),
            store=SQLiteOwnershipStore(tmp_path / "state.db"),
            application="test",
            schema=2,
            migrations={1: failed},
            config=HostConfig(development=True),
        )
        row: JSONValue = {
            "identity": ["test", "a" * 40, "principal", "b" * 32],
            "schema": 1,
            "payload": "5",
            "path": "/",
            "headers": {},
            "principal": None,
            "location": "/",
        }

        with pytest.raises(RuntimeError, match="state migration failed"):
            await Control(app).validate([row])
        assert app.runtime.graphs == set()
        await app.close()

    asyncio.run(run())


def test_state_preserving_disconnect_during_partial_freeze_releases_all_turns(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        app = DurableApplication(
            lambda state: native.P(state),
            initial=lambda: 0,
            codec=StateCodec(lambda value: value, integer),
            store=SQLiteOwnershipStore(tmp_path / "state.db"),
            application="test",
            config=HostConfig(development=True),
        )
        request = request_from_headers("/", [("Cookie", "pysx_browser=" + "b" * 32)])

        for _ in range(2):
            app.runtime.create_reserved(request, browser="b" * 32)
        graphs = [cast("DurableSession[int]", graph) for graph in app.runtime.graphs]
        held, release = asyncio.Event(), asyncio.Event()

        async def busy() -> None:
            async with graphs[-1].runtime.turn():
                held.set()
                await release.wait()

        working = asyncio.create_task(busy())
        await held.wait()

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        token = "t" * 40

        async with control_server(app, port, token):
            async with connect(
                f"ws://127.0.0.1:{port}",
                proxy=None,
                additional_headers={"Authorization": "Bearer " + token},
            ) as frozen:
                await frozen.send(json.dumps({"op": "freeze"}))

                async with asyncio.timeout(2):
                    while graphs[0].runtime.owner is None:
                        await asyncio.sleep(0.001)
            async with asyncio.timeout(2):
                while app.runtime.draining or any(
                    graph.runtime.owner is not None for graph in graphs[:-1]
                ):
                    await asyncio.sleep(0.001)
            release.set()
            await working

            for graph in graphs:
                def update(value: DurableSession[int] = graph) -> None:
                    value.state.set(8)

                await graph.runtime.update(update)
                assert graph.state() == 8
        await app.close()

    asyncio.run(run())


def test_local_preservation_keys_signals_by_call_site() -> None:
    from pysx import signal
    from pysx.lifecycle import Scopes, capture_local_state

    def body(branch: bool) -> None:
        signal(0)
        signal("text")

        for _ in range(3):
            signal(1.5)

        if branch:
            signal(None)

    capture_local_state(enable=True)

    try:
        scopes = Scopes()
        identity = object()

        with scopes.enter("root:", identity) as owner:
            body(True)
            first = sorted(owner.slots)
        assert len(first) == 6
        assert sum(key.endswith("#1") or key.endswith("#2") for key in first) == 2

        with scopes.enter("root:", identity) as again:
            body(True)
        assert again is owner
        assert sorted(again.slots) == first

        with scopes.enter("root:", identity) as narrowed:
            body(False)
        assert len(narrowed.slots) == 5
        assert set(narrowed.slots) < set(first)
    finally:
        capture_local_state(enable=False)

    other = Scopes()

    with other.enter("root:", object()) as disabled:
        body(True)
    assert disabled.slots == {}


def test_local_preservation_round_trips_component_signals() -> None:
    from pysx import native, signal
    from pysx.development_control import Control
    from pysx.hosting import Application, HostConfig
    from pysx.lifecycle import capture_local_state
    from pysx.request import RequestContext
    from pysx.session import Session

    def app() -> Fragment:
        count = signal(0)
        note = signal("draft")

        return native.Main(native.P(count, id="count"), native.P(note, id="note"))

    request = RequestContext(path="/", headers={"cookie": "pysx_browser=abc"})
    capture_local_state(enable=True)
    retired = Session(app, development=True, request=request)

    try:
        owner = retired.rendered.scopes.owners[""]
        assert len(owner.slots) == 2
        slots = sorted(owner.slots)
        owner.slots[slots[0]].publish(7)
        owner.slots[slots[1]].publish("edited")
        control = Control(Application(app, config=HostConfig(development=True)))
        rows: list[JSONValue] = [
            {
                "identity": control.local_identity("abc", None, "/"),
                "scopes": {
                    "": {
                        slot: [
                            cast("JSONValue", owner.slots[slot].published()),
                            owner.marks[slot],
                        ]
                        for slot in slots
                    }
                },
            }
        ]
        assert control.seed_local(rows) == "seeded"
        assert control.lookup_local("abc", None, "/") is not None
        assert control.lookup_local("abc", None, "/other") is None
        restored = Session(app, development=True, request=request)

        try:
            values = {
                slot: created.published()
                for slot, created in restored.rendered.scopes.owners[""].slots.items()
            }
            assert set(values.values()) == {7, "edited"}
        finally:
            restored.dispose()
    finally:
        retired.dispose()
        capture_local_state(enable=False)
        import pysx.lifecycle as module

        module.restore_source = None


def test_local_preservation_resets_when_the_initializer_changes(tmp_path: Path) -> None:
    import importlib.util
    import sys

    from pysx.development_control import Control
    from pysx.hosting import Application, HostConfig
    from pysx.lifecycle import capture_local_state
    from pysx.request import RequestContext
    from pysx.session import Session

    module_path = tmp_path / "widget.py"
    template = (
        "from pysx import native, signal\n\n\ndef app():\n"
        '    count = signal({0})\n    return native.Main(native.P(count, id="count"))\n'
    )

    def load() -> object:
        import linecache

        linecache.checkcache(str(module_path))
        importlib.invalidate_caches()
        spec = importlib.util.spec_from_file_location("widget", module_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["widget"] = module
        spec.loader.exec_module(module)

        return module.app

    request = RequestContext(path="/", headers={"cookie": "pysx_browser=abc"})
    bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    capture_local_state(enable=True)

    try:
        module_path.write_text(template.format(0))
        retired = Session(cast("Callable[[], Fragment]", load()), development=True, request=request)
        owner = retired.rendered.scopes.owners[""]
        slot = next(iter(owner.slots))
        owner.slots[slot].publish(42)
        control = Control(
            Application(cast("Callable[[], Fragment]", load()), config=HostConfig(development=True))
        )
        rows: list[JSONValue] = [
            {
                "identity": control.local_identity("abc", None, "/"),
                "scopes": {"": {slot: [42, owner.marks[slot]]}},
            }
        ]
        control.seed_local(rows)
        unchanged = Session(
            cast("Callable[[], Fragment]", load()), development=True, request=request
        )
        assert next(iter(unchanged.rendered.scopes.owners[""].slots.values())).published() == 42
        unchanged.dispose()
        module_path.write_text(template.format(5))
        edited = Session(cast("Callable[[], Fragment]", load()), development=True, request=request)
        scope = edited.rendered.scopes.owners[""]
        assert next(iter(scope.slots.values())).published() == 5
        assert set(scope.reset.values()) == {"initializer changed"}
        edited.dispose()
        retired.dispose()
    finally:
        sys.dont_write_bytecode = bytecode
        capture_local_state(enable=False)
        import pysx.lifecycle as module

        module.restore_source = None


def test_local_preservation_drops_values_that_cannot_be_serialized() -> None:
    from pysx import native, signal
    from pysx.development_control import Control
    from pysx.hosting import Application, HostConfig
    from pysx.lifecycle import capture_local_state
    from pysx.request import RequestContext
    from pysx.session import Session

    def app() -> Fragment:
        keep = signal(3)
        _opaque = signal(cast("int", object()))

        return native.Main(native.P(keep, id="keep"))

    request = RequestContext(path="/", headers={"cookie": "pysx_browser=abc"})
    capture_local_state(enable=True)
    session = Session(app, development=True, request=request)

    try:
        application = Application(app, config=HostConfig(development=True))
        application.runtime.graphs.add(session)
        control = Control(application)
        report = cast("dict[str, JSONValue]", control.freeze_local())
        assert report["dropped"] == 1
        rows = cast("list[dict[str, JSONValue]]", report["rows"])
        assert len(rows) == 1
        scopes = cast("dict[str, dict[str, JSONValue]]", rows[0]["scopes"])
        assert len(scopes[""]) == 1
    finally:
        session.dispose()
        capture_local_state(enable=False)


@pytest.mark.parametrize(
    ("limit", "reason"),
    [("_MAX_LOCAL_SLOTS", "slot limit"), ("_MAX_LOCAL_BYTES", "size limit")],
)
def test_local_preservation_bounds_degrade_to_a_fresh_reload(
    monkeypatch: pytest.MonkeyPatch, limit: str, reason: str
) -> None:
    from pysx import native, signal
    from pysx.development_control import Control
    from pysx.hosting import Application, HostConfig
    from pysx.lifecycle import capture_local_state
    from pysx.request import RequestContext
    from pysx.session import Session

    def app() -> Fragment:
        first = signal(1)
        second = signal("two")

        return native.Main(native.P(first, id="a"), native.P(second, id="b"))

    monkeypatch.setattr(f"pysx.development_control.{limit}", 1)
    request = RequestContext(path="/", headers={"cookie": "pysx_browser=abc"})
    capture_local_state(enable=True)
    session = Session(app, development=True, request=request)

    try:
        application = Application(app, config=HostConfig(development=True))
        application.runtime.graphs.add(session)
        report = cast("dict[str, JSONValue]", Control(application).freeze_local())
        assert report["bounded"] == reason
        assert report["rows"] == []
    finally:
        session.dispose()
        capture_local_state(enable=False)
