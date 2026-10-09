"""Exercise the operators example through its real session handlers."""

import re

from examples import examples
from examples.operators import app
from pysx.server import Session

CARDS = {
    "t1": ["t1-count-value", "doubled", "nested", "positional", "head"],
    "t2": [
        "t2-count-value",
        "both",
        "either",
        "negated",
        "tagged",
        "in-range",
        "tag-count",
        "greeting",
        "named-eq",
        "differs",
    ],
    "t3": [
        "t3-count-value",
        "over",
        "under",
        "at-least",
        "at-most",
        "over-named",
        "under-named",
        "at-least-named",
        "at-most-named",
    ],
    "t4": ["t4-count-value", "short-circuit", "lambda-not"],
}
READOUTS = [name for card in CARDS.values() for name in card]


def _slots(body: str) -> dict[str, str]:
    found: dict[str, str] = {}

    for name in READOUTS:
        match = re.search(rf'id="{name}"[^>]*><pysx-slot id="([^"]+)"', body)
        assert match is not None, f"missing readout {name}"
        found[name] = match[1]

    return found


def _handlers(body: str) -> dict[str, str]:
    return dict(re.findall(r'id="(t[1-4]-[a-z]+)"\s+data-pysx-click="([^"]+)"', body))


def _values(body: str) -> dict[str, str]:
    found: dict[str, str] = {}

    for name in READOUTS:
        match = re.search(rf'id="{name}"[^>]*><pysx-slot id="[^"]*">([^<]*)<', body)
        assert match is not None, f"missing value for {name}"
        found[name] = match[1]

    return found


def test_operators_example_renders_every_tier() -> None:
    assert examples["operators"] == "examples.operators:app"
    body = Session(app).rendered.body
    assert _values(body) == {
        "t1-count-value": "2",
        "doubled": "4",
        "nested": "10",
        "positional": "alpha",
        "head": "alpha",
        "t2-count-value": "2",
        "both": "False",
        "either": "False",
        "negated": "True",
        "tagged": "True",
        "in-range": "True",
        "tag-count": "2",
        "greeting": "hello ada",
        "named-eq": "True",
        "differs": "True",
        "t3-count-value": "2",
        "over": "True",
        "under": "True",
        "at-least": "True",
        "at-most": "True",
        "over-named": "True",
        "under-named": "True",
        "at-least-named": "True",
        "at-most-named": "True",
        "t4-count-value": "2",
        "short-circuit": "True",
        "lambda-not": "True",
    }


def test_operators_example_every_control_moves_its_own_section() -> None:
    body = Session(app).rendered.body
    handlers = _handlers(body)
    slots = _slots(body)
    assert len(handlers) == 13

    for button, handler in handlers.items():
        session = Session(app)

        if button.endswith("reset"):
            session.dispatch(handlers[f"{button.split('-')[0]}-count"], None)

        touched = {op["id"] for op in session.dispatch(handler, None) if op["op"] == "text"}
        card = CARDS[button.split("-")[0]]
        moved = [name for name in card if slots[name] in touched]
        # Every control changes a value in its own card, and not only the count readout.
        assert [name for name in moved if not name.endswith("count-value")], button


def test_operators_example_reset_restores_the_initial_state() -> None:
    session = Session(app)
    body = session.rendered.body
    handlers = _handlers(body)
    slots = _slots(body)
    names = {slot: name for name, slot in slots.items()}
    state = _values(body)
    initial = dict(state)

    for button in ("t1-count", "t1-nested", "t1-rotate", "t2-name", "t2-tag"):
        for op in session.dispatch(handlers[button], None):
            if op["op"] == "text":
                state[names[op["id"]]] = op["v"]

    assert state != initial

    for op in session.dispatch(handlers["t1-reset"], None):
        if op["op"] == "text":
            state[names[op["id"]]] = op["v"]

    assert state == initial
