"""Binding types, correction ordering, transactions and ownership."""

import re
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

import pytest

from pysx import Fragment, derived, html, native, signal
from pysx.forms import form_edits
from pysx.server import Session


def test_bindings_form_payloads_projections_and_echo() -> None:
    state = signal({"text": ["initial"], "other": ["quiet"]})
    text = state["text"][0]
    checked = signal(False)
    selected = signal(["a"])
    radio = signal("a")

    def view() -> Fragment:
        return native.Div(
            native.Input(bind_value=text, custom_attrs={"aria-checked": checked}),
            native.Input(type="checkbox", bind_checked=checked),
            native.Select(
                native.Option("A", value="a"),
                native.Option("B", value="b"),
                multiple=True,
                bind_selected=selected,
            ),
            native.Input(type="radio", value="a", bind_value=radio),
            native.Input(type="radio", value="b", bind_value=radio),
            native.Span(text),
            native.Span(state["other"][0]),
        )

    session = Session(view)
    handlers = list(session.rendered.bindings)
    state["other"].set(["changed"])
    assert len(session.pending) == 1
    assert session.dispatch(handlers[0], "typed") == [{"op": "text", "id": "f5:0", "v": "typed"}]
    assert state()["text"] == ["typed"]
    assert session.dispatch(handlers[1], True) == [
        {"op": "attr", "id": "f0:e1", "name": "aria-checked", "v": "true"}
    ]
    assert session.dispatch(handlers[2], ["b"]) == []
    assert selected() == ["b"]
    radio_ops = session.dispatch(handlers[4], "b")
    assert radio() == "b"
    assert radio_ops == [{"op": "attr", "id": "f3:e1", "name": "checked", "v": None}]
    session.dispose()
    assert not state.observers
    assert not checked.observers
    assert not selected.observers
    assert not session.rendered.bindings


def test_bindings_form_normalization_correction_even_without_state_change() -> None:
    text = signal("ABC")
    dirty = signal(False)

    def normalize(_value: object) -> None:
        text.set(text().upper())
        dirty.set(True)

    def view() -> Fragment:
        return native.Input(
            bind_value=text, on_input=normalize, custom_attrs={"aria-invalid": dirty}
        )

    session = Session(view)
    hid = next(iter(session.rendered.bindings))
    after = next(handler for handler in session.rendered.handlers if handler != hid)
    ops = session.dispatch(hid, "abc", 7, after=after)
    assert {"op": "attr", "id": "e1", "name": "value", "v": "ABC", "rev": 7} in ops
    assert {"op": "attr", "id": "e1", "name": "aria-invalid", "v": "true"} in ops
    assert session.dispatch(hid, "abC", 8, after=after) == [
        {"op": "attr", "id": "e1", "name": "value", "v": "ABC", "rev": 8}
    ]
    assert session.dispatch(hid, "ABC", 9, after=after) == []
    session.dispose()


def test_bindings_form_reset_atomic_validation_and_after_callback() -> None:
    text = signal("initial")
    checked = signal(True)
    received: list[object] = []

    def reset(value: object) -> None:
        received.append((text(), checked(), value))

    def view() -> Fragment:
        return native.Form(
            native.Input(bind_value=text),
            native.Input(type="checkbox", bind_checked=checked),
            on_reset=reset,
        )

    session = Session(view)
    ids = list(session.rendered.bindings)
    callback = next(hid for hid in session.rendered.handlers if hid not in ids)
    text.set("changed")
    checked.set(False)
    payload = {"entries": [["text", "initial"]], "valid": True}
    ops = session.dispatch(callback, payload, edits=[(ids[0], "initial", 2), (ids[1], True, 2)])
    assert ops == []
    assert received == [("initial", True, payload)]

    with pytest.raises(TypeError, match="payload"):
        session.dispatch(callback, None, edits=[(ids[0], "overwrite", 3), (ids[1], "bad", 3)])
    assert text() == "initial"
    assert checked() is True
    session.dispose()


@pytest.mark.parametrize(
    "source",
    [
        lambda: native.Input(bind_checked=signal(True)),
        lambda: native.Select(multiple=True, bind_value=signal("a")),
        lambda: native.Select(bind_selected=signal(["a"])),
        lambda: native.Input(type="radio", bind_value=signal("a")),
        lambda: native.Input(bind_value=derived(lambda: "readonly")),
    ],
)
def test_bindings_form_invalid_controls(source: object) -> None:
    with pytest.raises(TypeError):
        Session(cast("Callable[[], Fragment]", source))


def test_bindings_form_wrong_runtime_types_and_edits() -> None:
    def view() -> Fragment:
        return html(t"""\n        input(bindValue={signal(1)})\n        """)

    with pytest.raises(TypeError, match="payload"):
        Session(view)

    for edits in (1, [{"h": 1}], [{"h": "h0", "rev": True}], [{"h": "h0", "rev": -1}]):
        with pytest.raises(TypeError):
            form_edits(edits)
    assert form_edits([{"h": "h0", "v": ["a"], "rev": 1}]) == [("h0", ["a"], 1)]

    def multiple_bindings() -> Fragment:
        return native.Input(
            type="radio", value="a", bind_value=signal("a"), bind_checked=signal(True)
        )

    with pytest.raises(TypeError, match="one binding"):
        Session(multiple_bindings)


def test_bindings_form_conditional_cleanup_and_control_identity() -> None:
    visible = signal(True)
    text = signal("initial")

    def toggle_visible(_event: object) -> None:
        visible.set(not visible())

    def view() -> Fragment:
        return html(t"""
            if {visible}:
                input(bindValue={text})
            button(onClick={toggle_visible}): "Toggle"
        """)

    session = Session(view)
    toggle = re.findall(r'data-pysx-click="([^"]+)"', session.rendered.body)[0]
    binding = next(iter(session.rendered.bindings))
    assert session.dispatch(binding, "typed", 1) == []
    text.set("programmatic")
    assert all(op["op"] == "attr" for op in session.pending)

    for _ in range(5):
        session.dispatch(toggle, None)
        assert len(session.rendered.handlers) == 1
        assert not session.rendered.bindings
        assert not text.observers
        assert session.dispatch(binding, "stale") == []
        session.dispatch(toggle, None)
        assert len(session.rendered.handlers) == 2
        assert len(session.rendered.bindings) == 1
        assert text.observers
    session.dispose()
    assert not text.observers


def test_bindings_form_sessions_are_isolated() -> None:
    def view() -> Fragment:
        return native.Input(bind_value=signal("initial"))

    session = Session(view)
    other = Session(view)
    hid = next(iter(session.rendered.bindings))
    session.dispatch(hid, "first")
    assert other.rendered.bindings[hid].signal() == "initial"
    assert not other.pending
    session.dispose()
    other.dispose()
