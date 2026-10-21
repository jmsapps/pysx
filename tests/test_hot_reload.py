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
from pysx.development.control import Control, control_server
from pysx.hosting.hosting import request_from_headers

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pysx import Fragment, Signal
    from pysx.runtime.durable import DurableSession
    from pysx.runtime.wire import JSONValue

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

VOLATILE_SOURCE = (
    HOT_SOURCE.split("\napp = DurableApplication", 1)[0]
    .replace(
        "    derived, project, own_task, own_effect, on_mount)",
        "    derived, project, own_task, own_effect, on_mount, component, signal)",
    )
    .replace(
        "def view(state):",
        '@component\ndef app():\n    state = signal({"count": 0, "draft": "initial"})',
    )
)


def hot_project(root: Path, *, volatile: bool = False) -> None:
    (root / "user_app.py").write_text(VOLATILE_SOURCE if volatile else HOT_SOURCE)
    (root / "component.py").write_text(COMPONENT)
    (root / "style.css").write_text("color: rgb(255, 0, 0)")
    (root / "note.txt").write_text("template one")


def integer(value: JSONValue) -> int:
    if type(value) is not int:
        raise ValueError("expected integer")

    return value


def test_state_preserving_ordinary_base_path_change_uses_fresh_fallback(tmp_path: Path) -> None:
    from urllib.request import urlopen

    from test_dev_server import generation, get, project, resume_attachment, serving, wait_for

    project(tmp_path)
    source = (
        "from pysx import Application, HostConfig, native, signal\n\n"
        "def view():\n    count = signal(0)\n    return native.Main(native.P(count))\n\n"
        'app = Application(view, config=HostConfig(base_path="BASE"))\n'
    )
    (tmp_path / "user_app.py").write_text(source.replace("BASE", ""))

    with serving(tmp_path) as (port, log):
        with urlopen(f"http://127.0.0.1:{port}/", timeout=10) as response:
            cookie = response.headers.get("Set-Cookie", "").split(";")[0]
            response.read()
        asyncio.run(resume_attachment(port, cookie))
        before = generation(port)
        (tmp_path / "user_app.py").write_text(source.replace("BASE", "/next"))
        wait_for(lambda: generation(port) > before, log)
        status = json.loads(get(port, "/__pysx_dev__/status"))
        assert status["fresh_generation"] == status["generation"]
        assert b"pysx-root" in get(port, "/next/")


def test_local_preservation_restored_render_timeout_keeps_old_worker_usable(tmp_path: Path) -> None:
    from urllib.request import urlopen

    from test_dev_server import generation, get, project, resume_attachment, serving, wait_for

    project(tmp_path)
    source = (
        "from pysx import native, signal\nfrom component import label\n\n"
        "def app():\n    count = signal(0)\n    return native.Main(\n"
        '        native.H1(label(count()), id="label"), native.P(count, id="count"),\n'
        '        native.Button("+", onClick=lambda _event: count.set(count() + 1)))\n'
    )
    (tmp_path / "user_app.py").write_text(source)
    (tmp_path / "component.py").write_text('def label(_count):\n    return "healthy"\n')
    with serving(tmp_path, reload_timeout=3) as (port, log):
        with urlopen(f"http://127.0.0.1:{port}/", timeout=10) as response:
            cookie = response.headers.get("Set-Cookie", "").split(";")[0]
            response.read()
        attachment = asyncio.run(resume_attachment(port, cookie, clicks=1))
        before = generation(port)
        (tmp_path / "component.py").write_text(
            'import time\ndef label(count):\n    if count: time.sleep(30)\n    return "candidate"\n'
        )
        wait_for(lambda: bool(json.loads(get(port, "/__pysx_dev__/status"))["error"]), log)
        assert generation(port) == before
        assert b"healthy" in get(port)
        resumed = asyncio.run(resume_attachment(port, cookie, attachment=attachment))
        assert resumed["t"] == "repair"
        assert ">1</pysx-slot>" in str(resumed["html"])
        wait_for(lambda: len(json.loads(get(port, "/__pysx_dev__/status"))["workers"]) == 1, log)


def test_local_preservation_repeated_comprehension_sites_reset_conservatively() -> None:
    from pysx import signal
    from pysx.components.lifecycle import capture_local_state, restoring_local_state
    from pysx.runtime.session import Session

    def app() -> Fragment:
        values = [signal(index) for index in range(2)]

        return native.Main(*[native.P(value) for value in values])

    capture_local_state(enable=True)
    original = Session(app, development=True)

    try:
        owner = original.rendered.scopes.owners[""]
        held: dict[str, dict[str, object]] = {
            "": {slot: [9, owner.marks[slot], {"kind": owner.kinds[slot]}] for slot in owner.slots}
        }
        with restoring_local_state(held):
            restored = Session(app, development=True)

        try:
            scope = restored.rendered.scopes.owners[""]
            assert [value.published() for value in scope.slots.values()] == [0, 1]
            assert set(scope.reset.values()) == {"ambiguous call site"}
        finally:
            restored.dispose()
    finally:
        original.dispose()
        capture_local_state(enable=False)


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
    from pysx.components.lifecycle import Scopes, capture_local_state

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
    from pysx import local_state, native, signal
    from pysx.components.lifecycle import capture_local_state
    from pysx.development.control import Control
    from pysx.hosting.hosting import Application, HostConfig
    from pysx.hosting.request import RequestContext

    def app() -> Fragment:
        count = signal(0)
        note = signal("draft")
        visits = local_state("visits", 2)

        return native.Main(native.P(count, id="count"), native.P(note, id="note"), native.P(visits))

    async def run() -> None:
        request = RequestContext(path="/", headers={"cookie": "pysx_browser=abc"})
        application = Application(app, config=HostConfig(development=True))
        candidate = Application(app, config=HostConfig(development=True))
        capture_local_state(enable=True)

        try:
            retired = application.runtime.create_reserved(request)
            token = application.runtime.attach(retired, request)
            owner = retired.rendered.scopes.owners[""]
            assert len(owner.slots) == 3
            slots = sorted(slot for slot in owner.slots if not slot.startswith("named:"))
            owner.slots[slots[0]].publish(7)
            owner.slots[slots[1]].publish("edited")
            owner.slots["named:visits#0"].publish(8)
            snapshot = cast("dict[str, JSONValue]", Control(application).freeze_local())
            rows = cast("list[JSONValue]", json.loads(json.dumps(snapshot["rows"])))
            control = Control(candidate)
            await control.validate_local(rows)
            assert control.seed_local(rows) == "seeded"
            fresh = candidate.runtime.create_reserved(request)
            assert {
                value.published() for value in fresh.rendered.scopes.owners[""].slots.values()
            } == {0, "draft", 2}
            restored = candidate.runtime.resume(
                token, request, retired.transport.epoch, 0, location="/"
            )
            values = {
                created.published()
                for created in restored.rendered.scopes.owners[""].slots.values()
            }
            assert values == {7, "edited", 8}
            assert (
                restored.rendered.scopes.owners[""].values["visits"]
                is restored.rendered.scopes.owners[""].slots["named:visits#0"]
            )
            with pytest.raises(ValueError, match="resume handle"):
                candidate.runtime.resume(token, request, retired.transport.epoch, 0, location="/")
            assert candidate.runtime.reservations == 0
        finally:
            await application.close()
            await candidate.close()
            capture_local_state(enable=False)

    asyncio.run(run())


def test_local_preservation_resets_when_the_initializer_changes(tmp_path: Path) -> None:
    import importlib.util
    import sys

    from pysx.components.lifecycle import capture_local_state, restoring_local_state
    from pysx.hosting.request import RequestContext
    from pysx.runtime.session import Session

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
        held: dict[str, dict[str, object]] = {
            "": {slot: [42, owner.marks[slot], {"kind": owner.kinds[slot]}]}
        }
        with restoring_local_state(held):
            unchanged = Session(
                cast("Callable[[], Fragment]", load()), development=True, request=request
            )
        assert next(iter(unchanged.rendered.scopes.owners[""].slots.values())).published() == 42
        unchanged.dispose()
        module_path.write_text(template.format(5))
        with restoring_local_state(held):
            edited = Session(
                cast("Callable[[], Fragment]", load()), development=True, request=request
            )
        scope = edited.rendered.scopes.owners[""]
        assert next(iter(scope.slots.values())).published() == 5
        assert set(scope.reset.values()) == {"initializer changed"}
        edited.dispose()
        retired.dispose()
    finally:
        sys.dont_write_bytecode = bytecode
        capture_local_state(enable=False)


def test_local_preservation_drops_values_that_cannot_be_serialized() -> None:
    from pysx import native, signal
    from pysx.components.lifecycle import capture_local_state
    from pysx.development.control import Control
    from pysx.hosting.hosting import Application, HostConfig
    from pysx.hosting.request import RequestContext
    from pysx.runtime.session import Session

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
        application.runtime.attach(session, request)
        control = Control(application)
        report = cast("dict[str, JSONValue]", control.freeze_local())
        assert report["dropped"] == 1
        rows = cast("list[dict[str, JSONValue]]", report["rows"])
        assert len(rows) == 1
        scopes = cast("dict[str, dict[str, JSONValue]]", rows[0]["scopes"])
        assert len(scopes[""]) == 2
        assert any(
            isinstance(entry, list)
            and isinstance(entry[2], dict)
            and entry[2].get("reason") == "unsupported value type"
            for entry in scopes[""].values()
        )
    finally:
        session.dispose()
        capture_local_state(enable=False)


@pytest.mark.parametrize(
    ("limit", "reason"),
    [
        ("_MAX_LOCAL_SLOTS", "slot limit"),
        ("_MAX_LOCAL_BYTES", "size limit"),
        ("_MAX_LOCAL_SESSIONS", "session limit"),
    ],
)
def test_local_preservation_bounds_degrade_to_a_fresh_reload(
    monkeypatch: pytest.MonkeyPatch, limit: str, reason: str
) -> None:
    from pysx import native, signal
    from pysx.components.lifecycle import capture_local_state
    from pysx.development.control import Control
    from pysx.hosting.hosting import Application, HostConfig
    from pysx.hosting.request import RequestContext
    from pysx.runtime.session import Session

    def app() -> Fragment:
        first = signal(1)
        second = signal("two")

        return native.Main(native.P(first, id="a"), native.P(second, id="b"))

    monkeypatch.setattr(
        f"pysx.development.control.{limit}", 0 if limit == "_MAX_LOCAL_SESSIONS" else 1
    )
    request = RequestContext(path="/", headers={"cookie": "pysx_browser=abc"})
    capture_local_state(enable=True)
    session = Session(app, development=True, request=request)

    try:
        application = Application(app, config=HostConfig(development=True))
        application.runtime.graphs.add(session)
        application.runtime.attach(session, request)
        report = cast("dict[str, JSONValue]", Control(application).freeze_local())
        assert report["bounded"] == reason
        assert report["rows"] == []
        assert report["dropped"] == 2
    finally:
        session.dispose()
        capture_local_state(enable=False)


@pytest.mark.parametrize("mismatch", ["browser", "principal", "epoch", "revision", "location"])
def test_local_preservation_handoff_rejects_wrong_owner_without_consuming(mismatch: str) -> None:
    from dataclasses import replace

    from pysx import signal
    from pysx.components.lifecycle import capture_local_state
    from pysx.hosting.hosting import Application
    from pysx.hosting.request import RequestContext

    def app() -> Fragment:
        count = signal(0)

        return native.P(count)

    async def run() -> None:
        source = Application(app, config=HostConfig(development=True))
        target = Application(app, config=HostConfig(development=True))
        request = RequestContext(headers={"cookie": "pysx_browser=one"})
        capture_local_state(enable=True)

        try:
            graph = source.runtime.create_reserved(request)
            token = source.runtime.attach(graph, request)
            next(iter(graph.rendered.scopes.owners[""].slots.values())).set(9)
            snapshot = cast("dict[str, JSONValue]", Control(source).freeze_local())
            control = Control(target)
            control.seed_local(cast("list[JSONValue]", snapshot["rows"]))
            claimant = (
                replace(request, headers={"cookie": "pysx_browser=other"})
                if mismatch == "browser"
                else request
            )
            claimant = replace(claimant, principal="other") if mismatch == "principal" else claimant
            epoch = "wrong" if mismatch == "epoch" else graph.transport.epoch
            revision = graph.transport.revision + 1 if mismatch == "revision" else 0
            location = "/other" if mismatch == "location" else "/"

            with pytest.raises(ValueError, match="mismatched development handoff"):
                target.runtime.resume(token, claimant, epoch, revision, location=location)
            assert token in control.seeded
            restored = target.runtime.resume(token, request, graph.transport.epoch, 0, location="/")
            assert next(iter(restored.rendered.scopes.owners[""].slots.values()))() == 9
            assert token not in control.seeded
            assert target.runtime.reservations == 0
        finally:
            await source.close()
            await target.close()
            capture_local_state(enable=False)

    asyncio.run(run())


def test_local_preservation_same_cookie_tabs_and_unconsumed_generations() -> None:
    from pysx import signal
    from pysx.components.lifecycle import capture_local_state
    from pysx.hosting.hosting import Application
    from pysx.hosting.request import RequestContext

    def app() -> Fragment:
        count = signal(0)

        return native.P(count)

    async def run() -> None:
        applications = [Application(app, config=HostConfig(development=True)) for _ in range(4)]
        request = RequestContext(headers={"cookie": "pysx_browser=shared"})
        capture_local_state(enable=True)

        try:
            originals = [applications[0].runtime.create_reserved(request) for _ in range(2)]
            tokens = [applications[0].runtime.attach(graph, request) for graph in originals]

            for graph, value in zip(originals, (1, 3), strict=True):
                next(iter(graph.rendered.scopes.owners[""].slots.values())).set(value)
            snapshot = cast("dict[str, JSONValue]", Control(applications[0]).freeze_local())
            rows = cast("list[JSONValue]", json.loads(json.dumps(snapshot["rows"])))
            expires = [cast("dict[str, JSONValue]", row)["expires"] for row in rows]
            control = Control(applications[-1])

            for application in applications[1:]:
                control = Control(application)
                await control.validate_local(rows)
                control.seed_local(rows)
                snapshot = cast("dict[str, JSONValue]", control.freeze_local())
                rows = cast("list[JSONValue]", json.loads(json.dumps(snapshot["rows"])))
                assert [cast("dict[str, JSONValue]", row)["expires"] for row in rows] == expires
                assert len(application.runtime.graphs) == 0

            values: list[object] = []

            for token, original in zip(tokens, originals, strict=True):
                restored = applications[-1].runtime.resume(
                    token, request, original.transport.epoch, 0, location="/"
                )
                values.append(next(iter(restored.rendered.scopes.owners[""].slots.values()))())
            assert values == [1, 3]
            fresh = applications[-1].runtime.create_reserved(request)
            assert next(iter(fresh.rendered.scopes.owners[""].slots.values()))() == 0
            control.seed_local(rows)
            control.seeded[tokens[0]]["expires"] = 0

            with pytest.raises(ValueError, match="resume handle"):
                applications[-1].runtime.resume(
                    tokens[0], request, originals[0].transport.epoch, 0, location="/"
                )
            assert tokens[0] not in control.seeded
        finally:
            for application in applications:
                await application.close()
            capture_local_state(enable=False)

    asyncio.run(run())


def test_local_preservation_preflight_disposes_without_activating_resources() -> None:
    from pysx import on_cleanup, own_subscription, signal
    from pysx.components.lifecycle import capture_local_state
    from pysx.hosting.hosting import Application
    from pysx.hosting.request import RequestContext

    async def run() -> None:
        events: list[str] = []
        reject = False

        async def load() -> str:
            events.append("load")

            return "ready"

        async def background() -> None:
            events.append("task")
            await asyncio.Event().wait()

        def subscribe() -> Callable[[], None]:
            events.append("subscribe")

            return lambda: events.append("unsubscribe")

        def app() -> Fragment:
            count = signal(0)
            own_subscription("source", subscribe)
            own_effect("effect", lambda: events.append("effect"))
            own_task("task", background)
            resource("load", load)
            on_cleanup(lambda: events.append("dispose"))

            if reject and count() > 0:
                raise RuntimeError("restored state rejected")

            return native.P(count)

        application = Application(app, config=HostConfig(development=True))
        control = Control(application)
        request = RequestContext(headers={"cookie": "pysx_browser=owner"})
        capture_local_state(enable=True)

        try:
            await control.validate_local([])
            await asyncio.sleep(0)
            assert events == ["dispose"]
            assert not application.runtime.graphs
            assert not application.runtime.pending
            graph = application.runtime.create_reserved(request)
            application.runtime.attach(graph, request)
            next(
                value
                for value in graph.rendered.scopes.owners[""].slots.values()
                if value.published() == 0
            ).set(7)
            await asyncio.sleep(0.02)
            snapshot = cast("dict[str, JSONValue]", control.freeze_local())
            rows = cast("list[JSONValue]", json.loads(json.dumps(snapshot["rows"])))
            before = list(events)
            reject = True

            with pytest.raises(RuntimeError, match="restored state rejected"):
                await control.validate_local(rows)
            assert events == [*before, "dispose"]
            reject = False
            await control.validate_local(rows)
            await asyncio.sleep(0)
            assert events == [*before, "dispose", "dispose"]
            assert len(application.runtime.graphs) == 1
            assert not graph.closed
            assert len(graph.runtime.tasks) == 1
        finally:
            await application.close()
            capture_local_state(enable=False)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("initial", "value", "replacement", "reason"),
    [
        (0, 7, 0, None),
        (False, True, False, None),
        ([], [1, {"nested": [True, None, 1.5]}], [], None),
        ({}, {"new": [1, 2]}, {}, None),
        ((0,), (7,), (0,), "unsupported value type"),
        ({}, {1: "value"}, {}, "unsupported value type"),
        ([], [{"nested": (1, 2)}], [], "unsupported value type"),
        (0.0, float("nan"), 0.0, "unsupported value type"),
        (0, 7, "", "initializer type changed"),
        ({"nested": [0]}, {"nested": [7]}, {"nested": [""]}, "initializer type changed"),
        (0, "wrong", 0, "value type changed"),
        (False, 1, False, "value type changed"),
    ],
)
def test_local_preservation_lossless_types_and_unchanged_call_line(
    initial: object,
    value: object,
    replacement: object,
    reason: str | None,
) -> None:
    from pysx import native, signal
    from pysx.components.lifecycle import capture_local_state, restoring_local_state
    from pysx.development.control import Control
    from pysx.hosting.hosting import Application, HostConfig
    from pysx.hosting.request import RequestContext
    from pysx.runtime.session import Session

    def app() -> Fragment:
        signal(initial)

        return native.Main("safe value transport")

    request = RequestContext(headers={"cookie": "pysx_browser=owner"})
    application = Application(app, config=HostConfig(development=True))
    capture_local_state(enable=True)
    original = Session(app, development=True, request=request)
    application.runtime.graphs.add(original)
    application.runtime.attach(original, request)

    try:
        owner = original.rendered.scopes.owners[""]
        next(iter(owner.slots.values())).publish(value)
        report = cast("dict[str, JSONValue]", Control(application).freeze_local())
        rows = cast("list[dict[str, JSONValue]]", json.loads(json.dumps(report["rows"])))
        initial = replacement

        with restoring_local_state(cast("dict[str, dict[str, object]]", rows[0]["scopes"])):
            restored = Session(app, development=True, request=request)

        try:
            scope = restored.rendered.scopes.owners[""]
            actual = next(iter(scope.slots.values())).published()
            expected = value if reason is None else replacement
            assert actual == expected
            assert type(actual) is type(expected)
            assert list(scope.reset.values()) == ([] if reason is None else [reason])
        finally:
            restored.dispose()
    finally:
        original.dispose()
        capture_local_state(enable=False)


@pytest.mark.parametrize("change", ["absent", "malformed", "ambiguous"])
def test_local_preservation_refuses_uncertain_slot_metadata(change: str) -> None:
    from pysx import native, signal
    from pysx.components.lifecycle import capture_local_state, restoring_local_state
    from pysx.runtime.session import Session

    def app() -> Fragment:
        count = signal(0)

        return native.Main(count)

    capture_local_state(enable=True)
    original = Session(app, development=True)

    try:
        owner = original.rendered.scopes.owners[""]
        slot = next(iter(owner.slots))
        held: dict[str, dict[str, object]] = {"": {slot: [9, owner.marks[slot], {"kind": "int"}]}}

        if change == "absent":
            held[""].clear()
        elif change == "malformed":
            held[""][slot] = [9]
        else:
            held[""][slot] = [9, "", {"kind": "int"}]

        with restoring_local_state(held):
            graph = Session(app, development=True)

        try:
            scope = graph.rendered.scopes.owners[""]
            assert next(iter(scope.slots.values())).published() == 0
            assert list(scope.reset.values()) == [
                {
                    "absent": "absent",
                    "malformed": "malformed",
                    "ambiguous": "ambiguous call site",
                }[change]
            ]
        finally:
            graph.dispose()
    finally:
        original.dispose()
        capture_local_state(enable=False)


def test_local_preservation_reports_removed_scopes_and_slots() -> None:
    from pysx import signal
    from pysx.components.lifecycle import capture_local_state
    from pysx.hosting.hosting import Application
    from pysx.hosting.request import RequestContext

    def app() -> Fragment:
        signal(0)

        return native.Main("remaining scope")

    async def run() -> None:
        application = Application(app, config=HostConfig(development=True))
        request = RequestContext(headers={"cookie": "pysx_browser=owner"})
        capture_local_state(enable=True)

        try:
            graph = application.runtime.create_reserved(request)
            application.runtime.attach(graph, request)
            control = Control(application)
            frozen = cast("dict[str, JSONValue]", control.freeze_local())
            rows = cast("list[JSONValue]", json.loads(json.dumps(frozen["rows"])))
            row = cast("dict[str, JSONValue]", rows[0])
            scopes = cast("dict[str, dict[str, JSONValue]]", row["scopes"])
            entry = next(iter(scopes[""].values()))
            scopes[""]["removed-slot#0"] = entry
            scopes["removed-scope:"] = {"removed-owner-slot#0": entry}
            await control.validate_local(rows)
            report = cast("dict[str, JSONValue]", control.local_report())
            resets = cast("list[dict[str, JSONValue]]", report["reset"])
            assert {str(entry["reason"]) for entry in resets} == {"scope removed", "slot removed"}
            assert len(application.runtime.graphs) == 1
        finally:
            await application.close()
            capture_local_state(enable=False)

    asyncio.run(run())


def test_local_preservation_disconnect_thaws_partial_freeze() -> None:
    from pysx import signal
    from pysx.components.lifecycle import capture_local_state
    from pysx.hosting.hosting import Application
    from pysx.hosting.request import RequestContext

    def app() -> Fragment:
        count = signal(0)

        return native.P(count)

    async def run() -> None:
        application = Application(app, config=HostConfig(development=True))
        request = RequestContext(headers={"cookie": "pysx_browser=owner"})
        capture_local_state(enable=True)
        graphs = [application.runtime.create_reserved(request) for _ in range(2)]

        for graph in graphs:
            application.runtime.attach(graph, request)
        ordered = tuple(application.runtime.graphs)
        blocked = ordered[-1]
        await blocked.runtime.lock.acquire()
        token = "t" * 40

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]

        try:
            async with control_server(application, port, token):
                async with connect(
                    f"ws://127.0.0.1:{port}",
                    proxy=None,
                    additional_headers={"Authorization": "Bearer " + token},
                ) as client:
                    await client.send(json.dumps({"op": "freeze_local"}))

                    async with asyncio.timeout(2):
                        while not ordered[0].runtime.lock.locked():
                            await asyncio.sleep(0)
                    assert not application.runtime.reserve()
                async with asyncio.timeout(2):
                    while ordered[0].runtime.lock.locked() or application.runtime.draining:
                        await asyncio.sleep(0)
                blocked.runtime.lock.release()
                assert application.runtime.reserve()
                application.runtime.unreserve()
                await ordered[0].runtime.update(
                    lambda: next(iter(ordered[0].rendered.scopes.owners[""].slots.values())).set(4)
                )
                assert next(iter(ordered[0].rendered.scopes.owners[""].slots.values()))() == 4
        finally:
            if blocked.runtime.lock.locked():
                blocked.runtime.lock.release()
            await application.close()
            capture_local_state(enable=False)

    asyncio.run(run())
