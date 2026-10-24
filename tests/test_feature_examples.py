"""Runnable feature example acceptance using public handlers and the real launcher."""

from __future__ import annotations

import asyncio
import re
import signal
import socket
import urllib.request
from dataclasses import asdict
from typing import TYPE_CHECKING

import pytest
from acceptance_support import ready_server

from examples.browser_recovery import app as browser_recovery_app
from examples.composition import app as composition_app
from examples.diagnostics import app as diagnostics_app
from examples.events import app as events_app
from examples.forms import app as forms_app
from examples.keyed_rows import app as keyed_rows_app
from examples.navigation import app as navigation_app
from examples.reactive_state import app
from examples.reconnect import app as reconnect_app
from examples.recovery import app as recovery_app
from examples.resources import app as resources_app
from examples.session_security import app as security_app
from examples.templates import app as templates_app
from examples.themes import app as themes_app
from pysx import Application, BrowserEvent
from pysx.hosting import recovery_keys
from pysx.hosting.http import HTTPRuntime, reconcile
from pysx.hosting.request import RequestContext
from pysx.hosting.server import Session
from pysx.rendering.tree import apply, snapshot
from pysx.tooling.check import diagnostics

if TYPE_CHECKING:
    from pathlib import Path

    from pysx.runtime.durable import DurableSession


def test_browser_recovery_example_plain_state_restores_and_releases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def directory(_namespace: str) -> Path:
        return tmp_path / "keys"

    monkeypatch.setattr(recovery_keys, "default_directory", directory)

    async def exercise() -> None:
        request = RequestContext(headers={"host": "localhost", "cookie": "pysx_browser=example"})
        first = Application(browser_recovery_app)
        response = await first.http(request)
        assert response.status == 200
        assert response.adoption is not None
        graph = first.runtime.claim(response.adoption, request, "example")
        markup = graph.transport.tree.markup()

        def handler(identifier: str, attribute: str) -> str:
            element = re.search(rf'<[^>]*\bid="{identifier}"[^>]*>', markup)
            assert element is not None
            match = re.search(rf'{attribute}="([^"]+)"', element[0])
            assert match is not None

            return match[1]

        for identifier, value in (
            ("recovery-name", "Mira"),
            ("recovery-notes", "line one\nline two"),
        ):
            result = await graph.dispatch_result_async(
                handler(identifier, "data-pysx-binding"), value
            )
            assert result["status"] == "success"
        assert (
            await graph.dispatch_result_async(handler("add-revision", "data-pysx-click"), None)
        )["status"] == "success"
        service = first.runtime.browser_recovery
        assert service is not None
        published = service.publish(graph, request, force=True)
        token, document = published["capsule"], published["document"]
        assert isinstance(token, str)
        assert isinstance(document, str)
        assert graph.rendered.scopes.checkpoints.capture().resets == 0
        epoch = graph.transport.epoch
        await first.close()
        second = Application(browser_recovery_app)

        try:
            await second.start()
            service = second.runtime.browser_recovery
            assert service is not None
            restored = await service.reconstruct(second.runtime, request, token, document, "/")
            values = [slot.signal() for slot in restored.rendered.scopes.checkpoints.slots.values()]
            assert "Mira" in values
            assert "line one\nline two" in values
            assert 1 in values
            assert restored.transport.epoch != epoch
            assert service.checkpoint(restored).restored
        finally:
            await second.close()
        assert not second.runtime.graphs
        assert not restored.rendered.scopes.owners

    asyncio.run(exercise())


def test_browser_recovery_example_registered_and_checker_clean() -> None:
    from pathlib import Path

    from examples import examples

    assert examples["browser_recovery"] == "examples.browser_recovery:example"
    assert diagnostics(Path("examples/browser_recovery.py")) == []


@pytest.mark.acceptance
def test_browser_recovery_example_cli_startup_and_ctrl_c_exit() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    command = [
        "uv",
        "run",
        "--project",
        ".",
        "example",
        "run",
        "browser_recovery",
        "--port",
        str(port),
    ]
    with ready_server(command) as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            assert response.status == 200
            assert b"recovery-name" in response.read()
        server.send_signal(signal.SIGINT)
        assert server.wait(timeout=5) == 0


@pytest.mark.acceptance
@pytest.mark.parametrize("mode", ["standalone", "asgi", "watch"])
@pytest.mark.parametrize(
    "application", ["examples.browser_recovery:example", "examples.counter:app"]
)
def test_browser_recovery_hosting_and_unchanged_counter(mode: str, application: str) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    command = ["uv", "run", "--project", ".", "pysx", "--app", application, "--port", str(port)]

    if mode != "standalone":
        command.append("--" + mode)
    banner = "Uvicorn running" if mode == "asgi" else "pysx ready"
    with ready_server(command, ready_banner=banner) as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            markup = response.read()
            assert response.status == 200
            assert b'data-pysx-protocol="4"' in markup
            assert b'data-pysx-capsule="' in markup
            cookie = response.headers["set-cookie"].split(";", 1)[0]
        reset = urllib.request.Request(
            f"http://127.0.0.1:{port}/__pysx__/reset",
            data=b"",
            headers={"origin": f"http://127.0.0.1:{port}", "cookie": cookie, "x-pysx-reset": "1"},
            method="POST",
        )
        with urllib.request.urlopen(reset, timeout=5) as response:
            assert response.status == 204
            assert response.headers["set-cookie"].split(";", 1)[0] != cookie
        server.send_signal(signal.SIGINT)
        assert server.wait(timeout=8) == 0


def test_diagnostics_example_fix_warning_reset_isolation_and_cleanup() -> None:
    session, other = Session(diagnostics_app), Session(diagnostics_app)
    tree = snapshot(session.rendered.body, unwrap_row=False)

    def reading(markup: str) -> str:
        found = re.search(r'id="live-count"[^>]*>(.*?)</span>', markup)
        assert found is not None

        return re.sub(r"<[^>]+>", "", found[1])

    def click(identifier: str) -> None:
        found = re.search(rf'id="{identifier}"[^>]*data-pysx-click="([^"]+)"', tree.markup())
        assert found is not None
        result = asyncio.run(session.dispatch_result_async(found[1], None))
        assert result["status"] == "success"
        apply(tree, result["ops"])

    try:
        assert "template-freeze" in tree.markup()
        click("apply-fix")
        assert "No diagnostics ([])" in tree.markup()
        assert "count()" not in tree.markup()
        click("increment-counter")
        assert reading(tree.markup()) == "1"
        click("show-warning")
        assert "native-attribute" in tree.markup()
        click("reset-sample")
        assert "template-freeze" in tree.markup()
        assert reading(tree.markup()) == "1"
        assert "template-freeze" in other.rendered.body
        assert reading(other.rendered.body) == "0"
        assert other.pending == []
        assert session.pending_callbacks == 0
    finally:
        session.dispose()
        other.dispose()
    assert session.rendered.scopes.owners == {}
    assert session.rendered.handlers == {}
    assert session.rendered.watchers == []


def test_diagnostics_example_checker_clean_and_registered() -> None:
    from pathlib import Path

    from examples import examples

    assert examples["diagnostics"] == "examples.diagnostics:example"
    assert diagnostics(Path("examples/diagnostics.py")) == []
    assert diagnostics(Path("examples/components/diagnostics.py")) == []


@pytest.mark.acceptance
def test_diagnostics_example_cli_startup_and_ctrl_c_exit() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    command = ["uv", "run", "--project", ".", "example", "run", "diagnostics", "--port", str(port)]

    with ready_server(command) as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            assert response.status == 200
            assert b"Diagnostics" in response.read()
        server.send_signal(signal.SIGINT)
        assert server.wait(timeout=5) == 0


def test_resources_http_adoption_async_interactions_isolation_and_cleanup() -> None:
    async def exercise() -> None:
        runtime = HTTPRuntime(resources_app)
        request = RequestContext()
        initial = runtime.render(request, "first")
        assert b"Async resources" in initial.body
        assert b"Keep this draft" in initial.body
        assert b"<style" in initial.body
        assert initial.adoption is not None
        graph = runtime.pending[initial.adoption].session
        other_result = runtime.render(RequestContext(), "second")
        assert other_result.adoption is not None
        other = runtime.pending[other_result.adoption].session
        session = runtime.claim(initial.adoption, request, "first")
        assert session is graph
        tree = snapshot(initial.body.decode(), unwrap_row=False)

        async def settle(text: str) -> None:
            async with asyncio.timeout(2):
                while True:
                    apply(tree, session.pending)
                    session.pending.clear()
                    session.changed.clear()

                    if text in tree.markup():
                        return
                    await session.changed.wait()

        async def click(identifier: str) -> None:
            found = re.search(rf'id="{identifier}"[^>]*data-pysx-click="([^"]+)"', tree.markup())
            assert found is not None
            result = await session.dispatch_result_async(found[1], None)
            assert result["status"] == "success"
            apply(tree, result["ops"])

        try:
            binding = next(iter(session.rendered.bindings))
            reconcile(session, [(binding, "Private draft", 1)])
            with session.context():
                session.rendered.scopes.acknowledge(session.rendered.scopes.pending_mounts())
            await settle("Result 1")
            await settle("Server update received")
            assert "Private draft" in tree.markup()
            assert (
                other.rendered.bindings[next(iter(other.rendered.bindings))].signal()
                == "Keep this draft"
            )
            await click("reload-resource")
            await settle(">pending<")
            await click("cancel-resource")
            await settle(">cancelled<")
            await asyncio.sleep(0.35)
            apply(tree, session.pending)
            assert "Result 2" not in tree.markup()
            assert "cancelled" in tree.markup()
        finally:
            session.dispose()
            runtime.close()
            await asyncio.sleep(0)
        assert session.runtime.closed
        assert session.rendered.scopes.owners == {}
        assert session.rendered.handlers == {}
        assert other.runtime.closed

    asyncio.run(exercise())


def test_resources_checker_clean_and_registered() -> None:
    from pathlib import Path

    from examples import examples

    assert examples["resources"] == "examples.resources:example"
    assert diagnostics(Path("examples/resources.py")) == []


@pytest.mark.acceptance
def test_resources_cli_startup_and_ctrl_c_exit() -> None:
    command = ["uv", "run", "--project", ".", "example", "run", "resources", "--port", "8776"]
    with ready_server(command) as server:
        with urllib.request.urlopen("http://127.0.0.1:8776/", timeout=5) as response:
            assert response.status == 200
            assert b"Async resources" in response.read()
        server.send_signal(signal.SIGINT)
        assert server.wait(timeout=5) == 0


def test_recovery_interactions_isolation_and_cleanup() -> None:
    session, other = Session(recovery_app), Session(recovery_app)
    tree = snapshot(session.rendered.body, unwrap_row=False)

    def click(identifier: str) -> None:
        found = re.search(rf'id="{identifier}"[^>]*data-pysx-click="([^"]+)"', tree.markup())
        assert found is not None
        result = asyncio.run(session.dispatch_result_async(found[1], None))
        assert result["status"] == ("error" if identifier == "fail-panel" else "success")
        apply(tree, result["ops"])

    try:
        click("increment-panel")
        click("fail-panel")
        assert 'id="fallback"' in tree.markup()
        assert 'id="panel"' not in tree.markup()
        click("increment-healthy")
        assert other.pending == []
        assert 'id="fallback"' not in other.rendered.body
        click("retry")
        assert 'id="fallback"' not in tree.markup()
        assert 'id="panel"' in tree.markup()
        assert session.pending == []
        assert session.pending_callbacks == 0
    finally:
        session.dispose()
        other.dispose()
    assert session.rendered.scopes.owners == {}
    assert session.rendered.handlers == {}
    assert session.rendered.handler_failures == {}
    assert session.rendered.watchers == []
    assert session.rendered.dom.pending == {}


def test_recovery_checker_clean_and_registered() -> None:
    from pathlib import Path

    from examples import examples

    assert examples["recovery"] == "examples.recovery:example"
    assert diagnostics(Path("examples/recovery.py")) == []


@pytest.mark.acceptance
def test_recovery_cli_startup_and_ctrl_c_exit() -> None:
    command = ["uv", "run", "--project", ".", "example", "run", "recovery", "--port", "8775"]
    with ready_server(command) as server:
        with urllib.request.urlopen("http://127.0.0.1:8775/", timeout=5) as response:
            assert response.status == 200
            assert b"client.js" in response.read()
        server.send_signal(signal.SIGINT)
        assert server.wait(timeout=5) == 0


def test_pysx_12_st_4navigation_report_interactions_isolation_and_cleanup() -> None:
    session, other = Session(navigation_app), Session(navigation_app)
    router = session.routes.routers[0]
    markup = [session.rendered.body]

    def click(identifier: str, destination: str = "", *, navigation: bool = True) -> None:
        found = re.search(
            rf'id="{identifier}"[^>]*data-pysx-click="([^"]+)"', "\n".join(reversed(markup))
        )
        assert found is not None
        handler = found[1]
        event = asdict(BrowserEvent("click", handler, value=destination))
        ops = asyncio.run(session.dispatch_async(handler, None, event=event, navigation=navigation))

        for op in ops:
            if op["op"] == "list":
                markup.extend(op["html"].values())
            elif op["op"] == "html":
                markup.append(op["v"])

    try:
        click("user-one", "/users/1")
        assert router.params() == {"id": "1"}
        count = next(
            owner.values["count"]
            for owner in session.rendered.scopes.owners.values()
            if "count" in owner.values
        )
        click("user-two", "/users/2?tab=activity")
        assert router.params() == {"id": "2"}
        assert router.search() == "?tab=activity"
        assert any(
            owner.values.get("count") is count for owner in session.rendered.scopes.owners.values()
        )
        click("cancel", "/blocked")
        assert router.location().url == "/users/2?tab=activity"
        assert other.routes.routers[0].location().url == "/"
        assert other.pending == []
        router.navigate("./activity")
        assert router.params() == {"id": "2"}
        assert router.path() == "/users/2/activity"
        router.navigate("../")
        assert router.path() == "/users/2/"
        router.navigate("/files/docs/start#chapter")
        assert router.params() == {}
        assert router.hash() == "#chapter"
        session.routes.commands.clear()
        click("require-login", navigation=False)
        assert router.location().url == "/login"
        assert [(command["mode"], command["url"]) for command in session.routes.navigations()] == [
            ("replace", "/login")
        ]
        assert other.routes.routers[0].location().url == "/"
        assert other.pending == []
        session.routes.commands.clear()
        click("home", "/")
        assert router.path() == "/login"
        assert [(command["mode"], command["url"]) for command in session.routes.navigations()] == [
            ("push", "/"),
            ("replace", "/login"),
        ]
        session.routes.commands.clear()
        click("require-login", navigation=False)
        assert session.routes.commands == []
        click("login-continue", navigation=False)
        assert router.path() == "/"
        assert [(command["mode"], command["url"]) for command in session.routes.navigations()] == [
            ("push", "/")
        ]
    finally:
        session.dispose()
        other.dispose()
    assert session.rendered.scopes.owners == {}
    assert session.rendered.handlers == {}
    assert router.location.observers == {}
    assert router.path.observers == {}


def test_pysx_12_st_4navigation_report_checker_clean_and_registered() -> None:
    from pathlib import Path

    from examples import examples

    assert examples["navigation"] == "examples.navigation:example"
    assert diagnostics(Path("examples/navigation.py")) == []
    assert diagnostics(Path("examples/components/navigation.py")) == []


@pytest.mark.acceptance
def test_pysx_12_st_4navigation_report_cli_startup_and_ctrl_c_exit() -> None:
    command = ["uv", "run", "--project", ".", "example", "run", "navigation", "--port", "8768"]
    with ready_server(command) as server:
        with urllib.request.urlopen("http://127.0.0.1:8768/", timeout=5) as response:
            assert response.status == 200
            assert b"client.js" in response.read()
        server.send_signal(signal.SIGINT)
        assert server.wait(timeout=5) == 0


def test_templates_interactions_isolation_and_cleanup() -> None:
    session, other = Session(templates_app), Session(templates_app)

    def handler(attribute: str, value: str) -> str:
        match = re.search(
            rf'{attribute}="{value}"[^>]*data-pysx-click="([^"]+)"', session.rendered.body
        )
        assert match is not None

        return match[1]

    try:
        assert "1. Work" in session.rendered.body
        assert "While step 2" in session.rendered.body
        assert "Template(" not in session.rendered.body
        picked = handler("data-pick", "work:build")
        reversed_ops = session.dispatch(handler("id", "reverse"), None)
        assert any(op["op"] == "list" and op["keys"] == ["learn", "work"] for op in reversed_ops)
        assert any("1. Learn" in str(op) for op in reversed_ops)
        assert any(
            op["op"] == "text" and op["v"] == "work / build"
            for op in session.dispatch(picked, None)
        )
        assert other.pending == []
        assert any(
            "Compact details" in str(op) for op in session.dispatch(handler("id", "cycle"), None)
        )
        assert any("Quiet mode" in str(op) for op in session.dispatch(handler("id", "cycle"), None))
        assert any("extra-1" in str(op) for op in session.dispatch(handler("id", "add"), None))
        assert all(
            "While step" not in str(op) for op in session.dispatch(handler("id", "remove"), None)
        )
    finally:
        session.dispose()
        other.dispose()
    assert session.rendered.scopes.owners == {}
    assert session.rendered.handlers == {}


def test_templates_checker_clean_and_registered() -> None:
    from pathlib import Path

    from examples import examples

    assert examples["templates"] == "examples.templates:example"
    assert diagnostics(Path("examples/templates.py")) == []
    assert diagnostics(Path("examples/components/templates.py")) == []


@pytest.mark.acceptance
def test_templates_cli_startup_and_ctrl_c_exit() -> None:
    command = ["uv", "run", "--project", ".", "example", "run", "templates", "--port", "8761"]
    with ready_server(command) as server:
        with urllib.request.urlopen("http://127.0.0.1:8761/", timeout=5) as response:
            assert response.status == 200
            assert b"client.js" in response.read()
        server.send_signal(signal.SIGINT)
        assert server.wait(timeout=5) == 0


def test_components_example_interactions_isolation_and_cleanup() -> None:
    session, other = Session(composition_app), Session(composition_app)
    match = re.search(r'id="tree-library"[^>]*data-pysx-click="([^"]+)"', session.rendered.body)
    assert match is not None
    handler = match[1]
    initial = len(session.rendered.scopes.owners)

    try:
        session.rendered.scopes.acknowledge(session.rendered.scopes.pending_mounts())
        assert session.rendered.scopes.pending_mounts() == []
        opened = session.dispatch(handler, None, event=asdict(BrowserEvent("click", handler)))
        assert any(
            (
                op["op"] == "list"
                and any('id="tree-components"' in body for body in op["html"].values())
            )
            or (
                (op["op"] == "html" or op["op"] == "children") and 'id="tree-components"' in op["v"]
            )
            for op in opened
        )
        assert len(session.rendered.scopes.owners) > initial
        assert other.pending == []
        session.dispatch(handler, None, event=asdict(BrowserEvent("click", handler)))
        assert len(session.rendered.scopes.owners) == initial
    finally:
        session.dispose()
        other.dispose()
    assert session.rendered.scopes.owners == {}
    assert session.rendered.handlers == {}


def test_components_example_checker_clean() -> None:
    from pathlib import Path

    assert diagnostics(Path("examples/composition.py")) == []


@pytest.mark.acceptance
def test_components_example_cli_startup_cleanup() -> None:
    command = ["uv", "run", "--project", ".", "example", "run", "composition", "--port", "8759"]

    with (
        ready_server(command) as server,
        urllib.request.urlopen("http://127.0.0.1:8759/", timeout=5) as response,
    ):
        assert server.poll() is None
        assert response.status == 200
        assert b"client.js" in response.read()

    assert server.poll() is not None


def test_themes_example_interactions_isolation_and_cleanup() -> None:
    session, other = Session(themes_app), Session(themes_app)

    def handler(identifier: str) -> str:
        match = re.search(
            rf'id="{identifier}"[^>]*data-pysx-click="([^"]+)"', session.rendered.body
        )
        assert match is not None

        return match[1]

    try:
        before = other.rendered.css
        dark = session.dispatch(handler("dark"), None)
        assert any(op["op"] == "text" and op["v"] == "dark" for op in dark)
        assert "--ink:#f9fafb" in session.rendered.css
        assert other.rendered.css == before
        assert other.pending == []
        compact = session.dispatch(handler("compact"), None)
        assert any(op["op"] == "attr" and "12px" in (op["v"] or "") for op in compact)
        local = session.dispatch(handler("local-accent"), None)
        assert any(op["op"] == "attr" and "is-local" in (op["v"] or "") for op in local)
        reset = session.dispatch(handler("reset-vars"), None)
        assert any(op["op"] == "attr" and op["name"] == "style" and op["v"] is None for op in reset)
        clear = session.dispatch(handler("clear-theme"), None)
        assert any(op["op"] == "text" and op["v"] == "cleared" for op in clear)
        light = session.dispatch(handler("light"), None)
        assert any(op["op"] == "text" and op["v"] == "light" for op in light)
    finally:
        session.dispose()
        other.dispose()
    assert not session.rendered.handlers
    assert not session.rendered.styles.rules


def test_themes_example_checker_clean() -> None:
    from pathlib import Path

    assert diagnostics(Path("examples/themes.py")) == []
    assert diagnostics(Path("examples/components/themes.py")) == []


@pytest.mark.acceptance
def test_themes_example_cli_startup_cleanup() -> None:
    command = ["uv", "run", "--project", ".", "example", "run", "themes", "--port", "8760"]

    with (
        ready_server(command) as server,
        urllib.request.urlopen("http://127.0.0.1:8760/", timeout=5) as response,
    ):
        assert server.poll() is None
        assert response.status == 200
        assert b"client.js" in response.read()
        server.send_signal(signal.SIGINT)
        assert server.wait(timeout=5) == 0

    assert server.returncode == 0


def test_forms_example_interactions_isolation_and_cleanup() -> None:
    session = Session(forms_app)
    other = Session(forms_app)

    def binding(element: str) -> str:
        match = re.search(
            rf'<[^>]*id="{element}"[^>]*data-pysx-binding="([^"]+)"', session.rendered.body
        )
        assert match is not None

        return match[1]

    try:
        stylesheet = session.rendered.styles.snapshot()

        for element in (
            "form",
            "text",
            "note",
            "single",
            "multi",
            "disabled",
            "submit",
            "reset",
            "corrected",
            "program",
            "toggle",
            "owned",
        ):
            tag = re.search(rf'<[^>]*id="{element}"[^>]*>', session.rendered.body)
            assert tag is not None
            classes = re.search(r'class="([^"]+)"', tag[0])
            assert classes is not None
            assert any(f".{name} {{" in stylesheet for name in classes[1].split())

        text = binding("text")
        check = binding("check")
        single = binding("single")
        multiple = binding("multi")
        assert any(
            op["op"] == "text" and op["v"] == "typed" for op in session.dispatch(text, "typed")
        )
        assert any(op["op"] == "text" and op["v"] == "True" for op in session.dispatch(check, True))
        assert any(op["op"] == "text" and op["v"] == "b" for op in session.dispatch(single, "b"))
        assert any(
            op["op"] == "text" and op["v"] == "['a', 'b']"
            for op in session.dispatch(multiple, ["a", "b"])
        )
        assert other.rendered.bindings[text].signal() == "initial"
        assert other.pending == []
        assert len(session.rendered.bindings) == 9
    finally:
        session.dispose()
        other.dispose()
    assert not session.rendered.handlers
    assert session.dispatch(text, "late") == []


def test_forms_example_checker_clean() -> None:
    from pathlib import Path

    assert diagnostics(Path("examples/forms.py")) == []


def test_events_example_interactions_isolation_and_cleanup() -> None:
    session = Session(events_app)
    other = Session(events_app)
    match = re.search(
        r'<input[^>]*id="combo"[^>]*data-pysx-keydown="([^"]+)"', session.rendered.body
    )
    assert match is not None
    hid = match[1]

    try:
        down = session.dispatch(
            hid, None, event=asdict(BrowserEvent("keydown", hid, key="ArrowDown"))
        )
        assert any(
            op["op"] == "attr" and op["name"] == "aria-activedescendant" and op["v"] == "option-1"
            for op in down
        )
        selected = session.dispatch(
            hid, None, event=asdict(BrowserEvent("keydown", hid, key="Enter"))
        )
        assert any(op["op"] == "text" and op["v"] == "Blue" for op in selected)
        assert other.pending == []
        assert 'aria-expanded="false"' in other.rendered.body
        assert len(session.rendered.dom.mounts) == 4
    finally:
        session.dispose()
        other.dispose()
    assert not session.rendered.dom.nodes
    assert not session.rendered.event_handlers
    assert not session.rendered.dom.listeners
    assert session.dispatch(hid, None) == []


def test_events_example_checker_clean() -> None:
    from pathlib import Path

    assert diagnostics(Path("examples/events.py")) == []


@pytest.mark.acceptance
def test_events_example_cli_startup_cleanup() -> None:
    command = ["uv", "run", "--project", ".", "example", "run", "events", "--port", "8758"]

    with (
        ready_server(command) as server,
        urllib.request.urlopen("http://127.0.0.1:8758/", timeout=5) as response,
    ):
        assert server.poll() is None
        assert response.status == 200
        assert b"client.js" in response.read()

    assert server.poll() is not None


@pytest.mark.acceptance
def test_forms_example_cli_startup_cleanup() -> None:
    command = ["uv", "run", "--project", ".", "example", "run", "forms", "--port", "8757"]

    with (
        ready_server(command) as server,
        urllib.request.urlopen("http://127.0.0.1:8757/", timeout=5) as response,
    ):
        assert server.poll() is None
        assert response.status == 200
        assert b"client.js" in response.read()

    assert server.poll() is not None


def test_operators_example_interactions_and_isolation() -> None:
    session = Session(app)
    other = Session(app)
    handlers = list(session.rendered.handlers)
    other_handler = next(iter(other.rendered.handlers))

    try:
        assert "First score: 10" in session.rendered.body
        assert 'id="operator-size"' in session.rendered.body
        advanced = session.dispatch(handlers[0], None)
        assert [op["v"] for op in advanced if op["op"] == "text"] == ["3", "15", "True", "False"]
        assert any(op["op"] == "html" and "operator-branch" in op["v"] for op in advanced)
        first = session.dispatch(handlers[1], None)
        assert [op["v"] for op in first if op["op"] == "text"] == [
            "11",
            "First score: 11",
            "False",
            "True",
        ]
        assert any(op["op"] == "html" and op["v"] == "" for op in first)
        assert session.dispatch(handlers[3], None) == []
        rotated = session.dispatch(handlers[4], None)
        assert [op["v"] for op in rotated if op["op"] == "text"] == ["20, 30, 10", "20"]
        reset = session.dispatch(handlers[5], None)
        assert [op["v"] for op in reset if op["op"] == "text"] == ["1", "5", "False"]
        assert other.pending == []
        assert "First score: 10" in other.rendered.body
    finally:
        session.dispose()
        other.dispose()

    assert session.dispatch(handlers[0], None) == []
    assert other.dispatch(other_handler, None) == []


def test_operators_example_checker_clean() -> None:
    from pathlib import Path

    assert diagnostics(Path("examples/reactive_state.py")) == []


@pytest.mark.acceptance
def test_operators_example_cli_startup_cleanup() -> None:
    command = ["uv", "run", "--project", ".", "example", "run", "reactive_state", "--port", "8756"]

    with (
        ready_server(command) as server,
        urllib.request.urlopen("http://127.0.0.1:8756/", timeout=5) as response,
    ):
        assert server.poll() is None
        assert response.status == 200
        assert b"client.js" in response.read()

    assert server.poll() is not None


def test_keyed_rows_interactions_isolation_and_cleanup() -> None:
    session, other = Session(keyed_rows_app), Session(keyed_rows_app)

    def handler(
        attribute: str, value: str, *, binding: bool = False, markup: str | None = None
    ) -> str:
        marker = "binding" if binding else "click"
        found = re.search(
            rf'{attribute}="{value}"[^>]*data-pysx-{marker}="([^"]+)"',
            markup or session.rendered.body,
        )
        assert found is not None

        return found[1]

    draft_id = handler("id", "draft-alpha", binding=True)
    draft = session.rendered.bindings[draft_id].signal
    pick = handler("data-pick", "alpha")
    child = handler("data-child", "alpha:one")

    try:
        edited = session.dispatch(draft_id, "private draft")
        assert any(op["op"] == "text" and op["v"] == "private draft" for op in edited)
        assert all(op["op"] not in {"list", "children"} for op in edited)
        count = session.dispatch(handler("data-count", "alpha"), None)
        assert any(op["op"] == "text" and op["v"] == "1" for op in count)
        reversed_ops = session.dispatch(handler("id", "keyed-reverse"), None)
        assert len(reversed_ops) == 1
        assert reversed_ops[0]["op"] == "list"
        assert reversed_ops[0]["keys"] == ["beta", "alpha"]
        assert reversed_ops[0]["html"] == {}
        renamed = session.dispatch(handler("id", "keyed-rename"), None)
        assert all(op["op"] != "list" for op in renamed)
        assert session.rendered.bindings[draft_id].signal is draft
        assert draft() == "private draft"
        assert any(
            op["op"] == "text" and op["v"] == "Alpha renamed" for op in session.dispatch(pick, None)
        )
        assert any(
            op["op"] == "text" and op["v"] == "Alpha renamed / one"
            for op in session.dispatch(child, None)
        )
        added = session.dispatch(handler("data-add", "alpha"), None)
        assert any(op["op"] == "list" and len(op["html"]) == 1 for op in added)
        assert other.rendered.bindings[draft_id].signal() == "Alpha"
        assert other.pending == []
        session.dispatch(handler("id", "keyed-remove"), None)
        assert draft.observers == {}
        assert pick not in session.rendered.handlers
        restored = session.dispatch(handler("id", "keyed-restore"), None)
        markup = next(op["html"]["alpha"] for op in restored if op["op"] == "list")
        fresh_id = handler("id", "draft-alpha", binding=True, markup=markup)
        assert session.rendered.bindings[fresh_id].signal() == "Alpha restored"
        assert fresh_id != draft_id
        assert session.dispatch(pick, None) == []
    finally:
        session.dispose()
        other.dispose()
    assert session.rendered.handlers == {}
    assert session.rendered.dom.mounts == {}
    assert session.rendered.lists == {}
    assert session.rendered.scopes.owners == {}


def test_keyed_rows_checker_clean() -> None:
    from pathlib import Path

    assert diagnostics(Path("examples/keyed_rows.py")) == []


@pytest.mark.acceptance
def test_keyed_rows_cli_startup_and_ctrl_c_exit() -> None:
    command = ["uv", "run", "--project", ".", "example", "run", "keyed_rows", "--port", "8764"]
    with ready_server(command) as server:
        with urllib.request.urlopen("http://127.0.0.1:8764/", timeout=5) as response:
            assert response.status == 200
            assert b"client.js" in response.read()
        server.send_signal(signal.SIGINT)
        assert server.wait(timeout=5) == 0


def test_reconnect_current_state_interactions_isolation_and_cleanup() -> None:
    async def exercise() -> None:
        runtime = HTTPRuntime(reconnect_app)
        request = RequestContext()
        initial = runtime.render(request, "first")
        assert b"Bounded reconnect" in initial.body
        assert initial.adoption is not None
        other_result = runtime.render(RequestContext(), "second")
        assert other_result.adoption is not None
        other = runtime.pending[other_result.adoption].session
        session = runtime.claim(initial.adoption, request, "first")
        tree = snapshot(initial.body.decode(), unwrap_row=False)

        async def click(identifier: str) -> None:
            found = re.search(rf'id="{identifier}"[^>]*data-pysx-click="([^"]+)"', tree.markup())
            assert found is not None
            result = await session.dispatch_result_async(found[1], None)
            assert result["status"] == "success"
            apply(tree, result["ops"])
            session.transport.apply(result["ops"])

        def shown(markup: str, identifier: str) -> str:
            found = re.search(rf'id="{identifier}"[^>]*>(?:<[^>]*>)*([^<]*)<', markup)
            assert found is not None

            return found[1]

        def ticks(markup: str) -> int:
            return int(shown(markup, "reconnect-ticks"))

        try:
            binding = next(iter(session.rendered.bindings))
            reconcile(session, [(binding, "Edited draft", 1)])
            with session.context():
                session.rendered.scopes.acknowledge(session.rendered.scopes.pending_mounts())
            await click("reconnect-increment")
            assert shown(tree.markup(), "reconnect-clicks") == "1"
            session.pending.clear()
            session.changed.clear()

            # A detached browser accumulates no patches. The server keeps counting, and a
            # resuming socket adopts the complete current snapshot instead of a replay.
            session.detached = True
            started = ticks(str(session.transport.repair("")["html"]))
            async with asyncio.timeout(3):
                while ticks(str(session.transport.repair("")["html"])) < started + 2:
                    session.changed.clear()
                    await session.changed.wait()
            assert session.pending == []
            repaired = session.transport.repair(session.rendered.css)
            assert repaired["t"] == "repair"
            current = str(repaired["html"])
            assert "Edited draft" in current
            assert shown(current, "reconnect-clicks") == "1"
            assert ticks(current) >= started + 2
            assert "Edited draft" not in other.rendered.body
            assert other.transport.epoch != session.transport.epoch
            assert shown(other.rendered.body, "reconnect-clicks") == "0"
        finally:
            session.detached = False
            session.dispose()
            other.dispose()
            runtime.close()
            await asyncio.sleep(0)
        assert session.runtime.closed
        assert session.rendered.scopes.owners == {}
        assert session.rendered.handlers == {}
        assert session.rendered.watchers == []

    asyncio.run(exercise())


def test_reconnect_checker_clean_and_registered() -> None:
    from pathlib import Path

    from examples import examples

    assert examples["reconnect"] == "examples.reconnect:example"
    assert diagnostics(Path("examples/reconnect.py")) == []


@pytest.mark.acceptance
def test_reconnect_cli_startup_and_ctrl_c_exit() -> None:
    command = ["uv", "run", "--project", ".", "example", "run", "reconnect", "--port", "8777"]
    with ready_server(command) as server:
        with urllib.request.urlopen("http://127.0.0.1:8777/", timeout=5) as response:
            assert response.status == 200
            assert b"Bounded reconnect" in response.read()
        server.send_signal(signal.SIGINT)
        assert server.wait(timeout=5) == 0


def test_session_security_principals_authorization_isolation_and_cleanup() -> None:
    async def exercise() -> None:
        from pysx.hosting.hosting import request_from_headers

        writer = await security_app.http(request_from_headers("/?principal=alice", []))
        reader = await security_app.http(request_from_headers("/?principal=reader", []))
        other = await security_app.http(request_from_headers("/?principal=alice", []))
        assert writer.adoption is not None
        assert reader.adoption is not None
        assert other.adoption is not None
        first = security_app.runtime.pending[writer.adoption].session
        readonly = security_app.runtime.pending[reader.adoption].session
        sibling = security_app.runtime.pending[other.adoption].session

        async def write(session: Session) -> str:
            tree = snapshot(session.rendered.body, unwrap_row=False)
            handler = re.search(r'id="security-write"[^>]*data-pysx-click="([^"]+)"', tree.markup())
            assert handler is not None
            outcome = await session.dispatch_result_async(handler[1], None)
            assert outcome["status"] == "success"
            apply(tree, outcome["ops"])

            return tree.markup()

        try:
            updated = await write(first)
            assert "Allowed: your counter" in updated
            assert re.search(r'id="security-count"[^>]*>.*?>1<', updated)
            denied = await write(readonly)
            assert "Denied: this identity" in denied
            assert re.search(r'id="security-count"[^>]*>.*?>0<', denied)
            binding = next(iter(first.rendered.bindings))
            await first.dispatch_result_async(binding, "Writer-only draft", 1)
            assert next(iter(sibling.rendered.bindings.values())).signal() == "My private draft"
            assert next(iter(readonly.rendered.bindings.values())).signal() == "My private draft"
        finally:
            for token in (writer.adoption, reader.adoption, other.adoption):
                security_app.runtime.expire(token)
        assert first.closed
        assert readonly.closed
        assert sibling.closed
        assert not security_app.runtime.graphs

    asyncio.run(exercise())


def test_session_security_checker_clean_and_registered() -> None:
    from pathlib import Path

    from examples import examples

    assert examples["session_security"] == "examples.session_security:example"
    assert diagnostics(Path("examples/session_security.py")) == []


@pytest.mark.acceptance
def test_session_security_cli_startup_interaction_and_ctrl_c_exit() -> None:
    command = [
        "uv",
        "run",
        "--project",
        ".",
        "example",
        "run",
        "session_security",
        "--port",
        "8778",
    ]
    with ready_server(command) as server:
        with urllib.request.urlopen(
            "http://127.0.0.1:8778/?principal=reader", timeout=5
        ) as response:
            assert response.status == 200
            body = response.read()
            assert b"Session security" in body
            assert b"reader" in body
        server.send_signal(signal.SIGINT)
        assert server.wait(timeout=5) == 0


def test_durable_state_reconstruction_and_session_isolation(tmp_path: Path) -> None:
    from typing import cast

    from examples.durable_state import CODEC, ExampleState, create_application

    async def exercise() -> None:
        host = create_application(tmp_path / "state.db", worker="example-worker")
        await host.start()
        request = RequestContext(headers={"cookie": "pysx_browser=example-browser"})

        try:
            result = host.runtime.render(request, "example-browser")
            assert result.adoption is not None
            first = host.runtime.claim(result.adoption, request, "example-browser")
            typed = cast("DurableSession[ExampleState]", first)
            generation = re.search(
                r'id="durable-generation"[^>]*>(.*?)</span>', first.rendered.body
            )
            assert generation is not None
            handler = re.search(
                r'id="durable-increment"[^>]*data-pysx-click="([^"]+)"', first.rendered.body
            )
            assert handler is not None
            binding = next(iter(first.rendered.bindings))
            reconcile(first, [(binding, "A saved draft", 1)])
            update = await first.dispatch_result_async(handler[1], None)
            assert update["status"] == "success"
            first.checkpoint()
            saved = host.state_store.load(typed.snapshot.identity)
            assert saved is not None
            assert CODEC.loads(saved.payload) == ExampleState(1, "A saved draft")
            token = host.runtime.recovery_token(first)
            assert token is not None
            host.runtime.release(first)
            rebuilt = host.runtime.recover(token, request, location="/durable/recovered")
            assert "A saved draft" in rebuilt.rendered.body
            assert cast("DurableSession[ExampleState]", rebuilt).state().count == 1
            assert generation[1] not in rebuilt.rendered.body
            assert first.closed
            assert first.rendered.handlers == {}
            assert first.runtime.closed
            other = host.runtime.render(request, "example-browser")
            assert other.adoption is not None
            sibling = cast(
                "DurableSession[ExampleState]", host.runtime.pending[other.adoption].session
            )
            assert sibling.state() == ExampleState()
        finally:
            await host.close()
        assert not host.runtime.graphs
        assert host.renewal is not None
        assert host.renewal.done()

    asyncio.run(exercise())


def test_development_example_is_registered_and_clean() -> None:
    from pathlib import Path

    from examples import examples

    assert examples["development"] == "examples.development:example"
    assert diagnostics(Path("examples/development.py")) == []


@pytest.mark.acceptance
def test_development_cli_startup_and_ctrl_c_exit() -> None:
    from ownership_topology import free_port

    port = free_port()
    command = ["uv", "run", "--project", ".", "example", "run", "development", "--port", str(port)]

    with ready_server(command) as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            assert response.status == 200
            body = response.read()
            assert b"Try a hot update" in body
            assert b"development-state-count" in body
        server.send_signal(signal.SIGINT)
        assert server.wait(timeout=5) == 0


def test_durable_state_codec_rejects_unsupported_or_oversized_fields() -> None:
    from examples.durable_state import CODEC, ExampleState

    assert CODEC.loads(CODEC.dumps(ExampleState(3, "Saved"))) == ExampleState(3, "Saved")

    for payload in (b"null", b'{"count":true,"draft":"bad"}', b'{"count":1,"draft":null}'):
        with pytest.raises(ValueError, match=r"expected state object|invalid counter or draft"):
            CODEC.loads(payload)
    with pytest.raises(ValueError, match="invalid counter or draft"):
        CODEC.loads(CODEC.dumps(ExampleState(0, "x" * 513)))


def test_durable_state_checker_clean_and_registered() -> None:
    from pathlib import Path

    from examples import examples

    assert examples["durable_state"] == "examples.durable_state:example"
    assert diagnostics(Path("examples/durable_state.py")) == []


@pytest.mark.acceptance
def test_durable_state_cli_startup_and_ctrl_c_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ownership_topology import free_port

    port = free_port()
    monkeypatch.setenv("PYSX_STATE_PATH", str(tmp_path / "example.sqlite3"))
    command = [
        "uv",
        "run",
        "--project",
        ".",
        "example",
        "run",
        "durable_state",
        "--port",
        str(port),
    ]
    with ready_server(command) as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            assert response.status == 200
            assert b"Declared session state" in response.read()
        server.send_signal(signal.SIGINT)
        assert server.wait(timeout=5) == 0


def test_editor_features_example_is_registered_and_clean() -> None:
    from pathlib import Path

    from examples import examples

    assert examples["editor_features"] == "examples.editor_features:example"
    assert diagnostics(Path("examples/editor_features.py")) == []


def test_editor_features_demonstrates_every_advertised_interaction() -> None:
    """The example must actually exercise completion, hover, navigation and edits."""
    from pathlib import Path as FilePath

    from examples.editor_features import (
        SAMPLE,
        app,
        completion_report,
        formatted_sample,
        hover_report,
        navigation_report,
        outline_report,
        tokens_report,
    )
    from pysx import render
    from pysx.tooling.lsp.features import document_symbols, template_calls

    completion = completion_report()
    assert "tone" in completion, completion
    assert "title" in completion, completion
    hover = hover_report()
    assert "tone" in hover, hover
    assert "str" in hover, hover
    navigation = navigation_report()
    assert "'Panel'" in navigation
    assert "declared in this module: yes" in navigation
    tokens = tokens_report()
    assert "section" in tokens
    assert "keyword" in tokens
    name = str(FilePath("examples") / "_editor_sample.py")
    outline = outline_report(
        document_symbols(template_calls(SAMPLE, name, FilePath("examples"), {name: SAMPLE}))
    )
    assert "section" in outline
    formatted = formatted_sample()
    assert formatted != SAMPLE, "the sample is deliberately ragged"
    assert "Panel(title=" in formatted
    rendered = render(app)
    assert 'id="panel"' in rendered.body
    assert 'id="would-change"' in rendered.body
    assert "show-tokens" in rendered.body


@pytest.mark.acceptance
def test_editor_features_cli_startup_and_ctrl_c_exit() -> None:
    from ownership_topology import free_port

    port = free_port()
    command = [
        "uv",
        "run",
        "--project",
        ".",
        "example",
        "run",
        "editor_features",
        "--port",
        str(port),
    ]
    with ready_server(command) as server:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            assert response.status == 200
            body = response.read()
            assert b"Editor features" in body
            assert b"show-navigation" in body
        server.send_signal(signal.SIGINT)
        assert server.wait(timeout=5) == 0


def test_examples_share_one_favicon_asset() -> None:
    import importlib

    from examples import examples
    from examples.components import EXAMPLE_ASSETS

    asset = EXAMPLE_ASSETS["/favicon.ico"]
    assert asset.content_type == "image/png"
    assert asset.body.startswith(b"\x89PNG\r\n\x1a\n")

    for spec in examples.values():
        module_name, _, attribute = spec.partition(":")
        application = getattr(importlib.import_module(module_name), attribute)
        assert application.assets["/favicon.ico"] is asset
        assert "/client.js" in application.assets
