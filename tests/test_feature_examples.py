"""Runnable feature example acceptance using public handlers and the real launcher."""

import urllib.request

import pytest
from acceptance_support import ready_server

from examples.reactive_state import app
from pysx.check import diagnostics
from pysx.server import Session


def test_operators_example_interactions_and_isolation() -> None:
    session = Session(app)
    other = Session(app)
    handlers = list(session.rendered.handlers)

    try:
        assert "First score: 10" in session.rendered.body
        assert 'id="operator-size"' in session.rendered.body
        advanced = session.dispatch(handlers[0], None)
        assert [op["v"] for op in advanced if op["op"] == "text"] == ["3", "15", "True", "False"]
        assert any(op["op"] == "html" and "operator-branch" in op["v"] for op in advanced)
        first = session.dispatch(handlers[1], None)
        assert [op["v"] for op in first if op["op"] == "text"] == [
            "11", "First score: 11", "False", "True",
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
    assert other.dispatch(next(iter(other.rendered.handlers)), None) == []


def test_operators_example_checker_clean() -> None:
    from pathlib import Path

    assert diagnostics(Path("examples/reactive_state.py")) == []


@pytest.mark.acceptance
def test_operators_example_cli_startup_cleanup() -> None:
    command = ["uv", "run", "--project", ".", "example", "run", "reactive_state", "--port", "8756"]

    with ready_server(command) as server, urllib.request.urlopen(
        "http://127.0.0.1:8756/", timeout=5
    ) as response:
        assert server.poll() is None
        assert response.status == 200
        assert b"client.js" in response.read()

    assert server.poll() is not None
