"""Event snapshot validation, policy serialization and compatibility."""

from dataclasses import asdict

import pytest

from pysx import BrowserEvent, Fragment, native, on_event, pysx, signal
from pysx.events import FIELD_LIMIT, VALUE_LIMIT, decode_event
from pysx.forms import PayloadError
from pysx.server import Session


@pytest.mark.parametrize("kind", ["click", "keydown", "pointerdown", "focus", "submit", "custom"])
def test_events_immediate_families(kind: str) -> None:
    value = BrowserEvent(kind, "h1", key="ArrowDown", shift=True, x=12.5)
    assert decode_event(asdict(value), "h1", kind) == value


@pytest.mark.parametrize(
    "fields",
    [
        {"key": 4},
        {"shift": 1},
        {"x": float("nan")},
        {"x": 10**1000},
        {"button": True},
        {"unknown": "bad"},
        {"key": "x" * (FIELD_LIMIT + 1)},
        {"value": "x" * (VALUE_LIMIT + 1)},
        {"handler": "other"},
        {"type": "keyup"},
    ],
)
def test_events_immediate_reject_invalid(fields: dict[str, object]) -> None:
    snapshot = asdict(BrowserEvent("keydown", "h1")) | fields
    with pytest.raises(PayloadError):
        decode_event(snapshot, "h1", "keydown")


def test_events_immediate_accepts_a_long_bound_value() -> None:
    text = "x" * (FIELD_LIMIT * 4)
    snapshot = asdict(BrowserEvent("input", "h1")) | {"value": text}
    assert decode_event(snapshot, "h1", "input").value == text


def test_events_immediate_reset_applies_edits_before_the_typed_callback() -> None:
    text = signal("initial")
    seen: list[tuple[str, BrowserEvent]] = []

    def view() -> Fragment:
        return native.Form(
            native.Input(bind_value=text),
            on_reset=on_event(lambda event: seen.append((text(), event))),
        )

    session = Session(view)
    binding = next(iter(session.rendered.bindings))
    callback = next(iter(session.rendered.event_handlers))
    text.set("edited")
    snapshot = asdict(BrowserEvent("reset", callback))
    assert session.dispatch(callback, None, edits=[(binding, "initial", 2)], event=snapshot) == []
    assert [value for value, _event in seen] == ["initial"]
    assert seen[0][1].type == "reset"
    assert text() == "initial"
    session.dispose()


def test_events_immediate_dispatch_and_legacy() -> None:
    received: list[BrowserEvent] = []

    def app() -> Fragment:
        return native.Button("press", on_keydown=on_event(received.append, keys=("Enter",)))

    session = Session(app)
    hid = next(iter(session.rendered.event_handlers))
    session.dispatch(hid, None, event=asdict(BrowserEvent("keydown", hid, key="Enter")))
    assert received[0].key == "Enter"
    with pytest.raises(PayloadError):
        session.dispatch(hid, None, event={"type": "click", "handler": hid})
    session.dispose()
    assert not session.rendered.event_handlers


def test_events_immediate_policy_and_owned_removal() -> None:
    visible = signal(True)
    handler = on_event(lambda _event: None, prevent_default=True, stop_propagation=True)

    def app() -> Fragment:
        return pysx(t"""
            if {visible}:
                input(onKeyDown={handler})
        """)

    session = Session(app)
    assert "data-pysx-policy-keydown" in session.rendered.body
    visible.set(False)
    assert not session.rendered.event_handlers
    session.dispose()


def test_accessible_keyboard_binding_before_typed_callback() -> None:
    text = signal("initial")
    seen: list[tuple[str, str]] = []

    def app() -> Fragment:
        return native.Input(
            bind_value=text, on_input=on_event(lambda event: seen.append((text(), event.value)))
        )

    session = Session(app)
    binding = next(iter(session.rendered.bindings))
    callback = next(iter(session.rendered.event_handlers))
    session.dispatch(
        binding,
        "edited",
        after=callback,
        event=asdict(BrowserEvent("input", callback, value="edited")),
    )
    assert seen == [("edited", "edited")]
    session.dispose()
