"""Failed dispatch commits state and drains transient work."""

import asyncio
import json
import re
import sys
from typing import TYPE_CHECKING

import pytest
from acceptance_support import ready_server, receive
from websockets.asyncio.client import connect

from examples.todos import app as todos_app
from pysx import native, signal
from pysx.hosting.server import Session
from pysx.rendering.tree import apply, snapshot
from pysx.runtime.forms import PayloadError

if TYPE_CHECKING:
    from pysx import Fragment


@pytest.mark.parametrize("after_write", [False, True])
def test_local_failure_handler_repairs(
    after_write: bool,
) -> None:
    value = signal(0)

    def bad(_: object) -> None:
        if after_write:
            value.set(value() + 1)

        raise RuntimeError("private secret")

    def good(_: object) -> None:
        value.set(value() + 1)

    def app() -> Fragment:
        return native.Div(
            native.Button("bad", on_click=bad),
            native.Button("good", on_click=good),
            native.P(value),
        )

    session, other = Session(app), Session(lambda: native.P(signal(0)))
    bad_id, good_id = session.rendered.handlers

    try:
        failed = asyncio.run(session.dispatch_result_async(bad_id, None))
        assert failed["status"] == "error"
        assert "error" in failed
        assert failed["error"]["owner"] is None
        assert "private" not in str(failed)
        assert value() == int(after_write)
        assert bool(failed["ops"]) is after_write
        assert other.pending == []
        assert session.pending == []
        assert session.pending_callbacks == 0
        passed = asyncio.run(session.dispatch_result_async(good_id, None))
        assert passed["status"] == "success"
        assert value() == int(after_write) + 1
    finally:
        session.dispose()
        other.dispose()


def test_local_failure_user_payload_exception_is_application_failure() -> None:
    def bad(_: object) -> None:
        raise PayloadError("user failure")

    session = Session(lambda: native.Button("bad", on_click=bad))

    try:
        result = asyncio.run(
            session.dispatch_result_async(next(iter(session.rendered.handlers)), None)
        )
        assert result["status"] == "error"
    finally:
        session.dispose()


def test_local_failure_cancel_keeps_committed_ops() -> None:
    async def exercise() -> None:
        value = signal(0)

        async def cancel(_: object) -> None:
            value.set(1)
            await asyncio.sleep(0)

            raise asyncio.CancelledError

        def good(_: object) -> None:
            value.set(2)

        session = Session(
            lambda: native.Div(
                native.Button("cancel", on_click=cancel),
                native.Button("good", on_click=good),
                native.P(value),
            )
        )
        first, second = session.rendered.handlers

        try:
            with pytest.raises(asyncio.CancelledError):
                await session.dispatch_result_async(first, None)
            assert session.pending_callbacks == 0
            assert value() == 1
            result = await session.dispatch_result_async(second, None)
            assert result["status"] == "success"
            assert [op["v"] for op in result["ops"] if op["op"] == "text"] == ["1", "2"]
            assert session.pending == []
        finally:
            session.dispose()

    asyncio.run(exercise())


def test_local_failure_binding_correction_has_revision() -> None:
    value = signal("initial")

    def fail(_: object) -> None:
        value.set("normalized")

        raise ValueError("private")

    session = Session(
        lambda: native.Div(native.Input(bind_value=value), native.Button("bad", on_click=fail))
    )
    binding, click = session.rendered.handlers

    try:
        result = asyncio.run(
            session.dispatch_result_async(click, None, edits=[(binding, "edit", 8)])
        )
        assert result["status"] == "error"
        assert any(
            op["op"] == "attr" and op.get("rev") == 8 and op["v"] == "normalized"
            for op in result["ops"]
        )
        invalid = asyncio.run(session.dispatch_result_async(binding, 5, 9))
        assert invalid["status"] == "invalid"
        assert any(
            op["op"] == "attr" and op.get("rev") == 9 and op["v"] == "normalized"
            for op in invalid["ops"]
        )
    finally:
        session.dispose()


@pytest.mark.parametrize("development", [False, True])
def test_local_failure_safe_client_and_development_source_logs(
    development: bool, caplog: pytest.LogCaptureFixture
) -> None:
    def bad(_: object) -> None:
        raise RuntimeError("private secret")

    session = Session(lambda: native.Button("bad", on_click=bad), development=development)

    try:
        result = asyncio.run(
            session.dispatch_result_async(next(iter(session.rendered.handlers)), None)
        )
        assert result["status"] == "error"
        assert "private" not in str(result)
        assert bool(caplog.records[-1].exc_info) is development
        assert "private" not in caplog.records[-1].getMessage()
    finally:
        session.dispose()


@pytest.mark.acceptance
def test_local_failure_real_wire_outcomes_keep_connection_and_other_session() -> None:
    port = 8773
    command = [
        sys.executable,
        "-m",
        "pysx.hosting.server",
        "--app",
        "tests.browser_recovery_fixture:app",
        "--port",
        str(port),
    ]

    async def exercise() -> None:
        async with (
            connect(f"ws://127.0.0.1:{port}/ws") as ws,
            connect(f"ws://127.0.0.1:{port}/ws") as other,
        ):
            initial, second = await receive(ws), await receive(other)
            assert initial["t"] == "init"
            assert second["t"] == "init"
            fail = re.search(r'id="fail"[^>]*data-pysx-click="([^"]+)"', initial["html"])
            healthy = re.search(r'id="healthy"[^>]*data-pysx-click="([^"]+)"', initial["html"])
            assert fail is not None
            assert healthy is not None
            await ws.send(json.dumps({"t": "event", "h": fail[1]}))
            outcome = await receive(ws)
            assert outcome["t"] == "result"
            assert outcome["status"] == "error"
            assert any(op["op"] == "html" and "Retry" in op["v"] for op in outcome["ops"])
            await ws.send(json.dumps({"t": "event", "h": fail[1]}))
            stale = await receive(ws)
            assert stale["t"] == "result"
            assert stale["status"] == "stale"
            await ws.send("invalid JSON")
            invalid = await receive(ws)
            assert invalid["t"] == "result"
            assert invalid["status"] == "invalid"
            await ws.send(json.dumps({"t": "event", "h": healthy[1]}))
            repaired = await receive(ws)
            assert repaired["t"] == "patch"
            assert any(op["op"] == "text" and op["v"] == "1" for op in repaired["ops"])
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(other.recv(), 0.05)

    with ready_server(command):
        asyncio.run(exercise())


def test_fallback_empty_todos_restores_keys_with_live_new_generations() -> None:
    session = Session(todos_app)
    tree = snapshot(session.rendered.body, unwrap_row=False)

    def click(label: str) -> None:
        found = re.search(
            rf'<button[^>]*data-pysx-click="([^"]+)"[^>]*>{label}</button>', tree.markup()
        )
        assert found is not None
        result = asyncio.run(session.dispatch_result_async(found[1], None))
        assert result["status"] == "success"
        apply(tree, result["ops"])

    watcher = next(iter(session.rendered.lists.values()))
    first = watcher.rows["1"]

    try:
        click("Active")
        assert watcher.rows["1"] is first
        click("Completed")
        click("Clear completed")
        assert "No completed todos" in tree.markup()
        assert watcher.rows == {}
        click("All")
        assert watcher.order == ["1", "2"]
        assert watcher.rows["1"] is not first
        assert watcher.rows["1"].prefix != first.prefix
        assert 'id="todo-empty"' not in tree.markup()
        assert session.pending == []
    finally:
        session.dispose()


def test_local_failure_stale_handler_still_applies_binding_edits() -> None:
    value = signal("initial")
    session = Session(lambda: native.Div(native.Input(bind_value=value)))
    (binding,) = session.rendered.handlers

    try:
        result = asyncio.run(
            session.dispatch_result_async("", None, None, edits=[(binding, "restored", 4)])
        )
        assert result["status"] == "stale"
        assert value() == "restored"
        unknown = asyncio.run(session.dispatch_result_async("h404", None))
        assert unknown["status"] == "stale"
        assert unknown["ops"] == []
    finally:
        session.dispose()
