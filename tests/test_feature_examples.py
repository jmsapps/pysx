"""Runnable feature example acceptance using public handlers and the real launcher."""

import asyncio
import re
import signal
import urllib.request
from dataclasses import asdict

import pytest
from acceptance_support import ready_server

from examples.composition import app as composition_app
from examples.events import app as events_app
from examples.forms import app as forms_app
from examples.navigation import app as navigation_app
from examples.reactive_state import app
from examples.templates import app as templates_app
from examples.themes import app as themes_app
from pysx import BrowserEvent
from pysx.check import diagnostics
from pysx.server import Session


def test_pysx_12_st_4_navigation_interactions_isolation_and_cleanup() -> None:
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
        assert [(command["mode"], command["url"]) for command in session.routes.commands] == [
            ("replace", "/login")
        ]
        assert other.routes.routers[0].location().url == "/"
        assert other.pending == []
        session.routes.commands.clear()
        click("home", "/")
        assert router.path() == "/login"
        assert [(command["mode"], command["url"]) for command in session.routes.commands] == [
            ("push", "/"),
            ("replace", "/login"),
        ]
        session.routes.commands.clear()
        click("require-login", navigation=False)
        assert session.routes.commands == []
        click("login-continue", navigation=False)
        assert router.path() == "/"
        assert [(command["mode"], command["url"]) for command in session.routes.commands] == [
            ("push", "/")
        ]
    finally:
        session.dispose()
        other.dispose()
    assert session.rendered.scopes.owners == {}
    assert session.rendered.handlers == {}
    assert router.location.observers == {}
    assert router.path.observers == {}


def test_pysx_12_st_4_navigation_checker_clean_and_registered() -> None:
    from pathlib import Path

    from examples import examples

    assert examples["navigation"] == "examples.navigation:app"
    assert diagnostics(Path("examples/navigation.py")) == []
    assert diagnostics(Path("examples/components/navigation.py")) == []


@pytest.mark.acceptance
def test_pysx_12_st_4_navigation_cli_startup_and_ctrl_c_exit() -> None:
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

    assert examples["templates"] == "examples.templates:app"
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
            op["op"] == "list"
            and any('id="tree-components"' in body for body in op["html"].values())
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
