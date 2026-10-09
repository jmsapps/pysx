"""Exercise the runnable state example through its real session handlers."""

from examples import examples
from examples.reactive_state import app
from pysx.server import Session


def test_reactive_state_example() -> None:
    assert examples["reactive_state"] == "examples.reactive_state:app"
    session = Session(app)
    handlers = list(session.rendered.handlers)
    assert len(handlers) == 6
    assert "Reactive state" in session.rendered.body
    advanced = session.dispatch(handlers[0], None)
    assert [op["v"] for op in advanced if op["op"] == "text"] == ["3", "15", "True", "False"]
    first = session.dispatch(handlers[1], None)
    assert [op["v"] for op in first if op["op"] == "text"] == [
        "11",
        "First score: 11",
        "False",
        "True",
    ]
    second = session.dispatch(handlers[2], None)
    assert [op["v"] for op in second if op["op"] == "text"] == ["21"]
    assert session.dispatch(handlers[3], None) == []
    rotated = session.dispatch(handlers[4], None)
    assert [op["v"] for op in rotated if op["op"] == "text"] == ["20, 30, 10", "20"]
    reset = session.dispatch(handlers[5], None)
    assert [op["v"] for op in reset if op["op"] == "text"] == ["1", "5", "False"]
    session.dispose()
