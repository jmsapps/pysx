"""A reproducible form fixture for browser interaction and lifecycle checks."""

import json
from typing import cast

from pysx import Fragment, html, native, signal


def app() -> Fragment:
    state = signal({"text": ["initial"]})
    text = state["text"][0]
    note = signal("note")
    checked = signal(False)
    single = signal("a")
    selected = signal(["a"])
    radio = signal("a")
    corrected = signal("ABC")
    status = signal("ready")
    resets = signal(0)
    invalids = signal(0)
    visible = signal(True)
    owned = signal("owned")

    def normalize(_value: object) -> None:
        corrected.set(corrected().upper())

    def submitted(value: object) -> None:
        status.set(json.dumps(cast("dict[str, object]", value), sort_keys=True))

    def reset(_value: object) -> None:
        resets.update(lambda count: count + 1)

    def invalid(_value: object) -> None:
        invalids.update(lambda count: count + 1)

    def programmatic(_value: object) -> None:
        text.set("server")
        note.set("server note")
        checked.set(True)
        single.set("b")
        selected.set(["b"])
        radio.set("b")

    def toggle(_value: object) -> None:
        visible.set(not visible())

    conditional = html(t"""
        if {visible}:
            input(id="owned" bindValue={owned})
    """)

    return native.Div(
        native.Form(
            native.Input(
                id="text", name="text", required=True, bind_value=text, on_invalid=invalid
            ),
            native.Textarea(id="note", name="note", bind_value=note),
            native.Input(
                id="check", name="check", type="checkbox", value="yes", bind_checked=checked
            ),
            native.Select(
                native.Option("A", value="a"),
                native.Option("B", value="b"),
                id="single",
                name="single",
                bind_value=single,
            ),
            native.Select(
                native.Option("A", value="a"),
                native.Option("B", value="b"),
                id="multi",
                name="color",
                multiple=True,
                bind_selected=selected,
            ),
            native.Input(id="radio-a", type="radio", name="radio", value="a", bind_value=radio),
            native.Input(id="radio-b", type="radio", name="radio", value="b", bind_value=radio),
            native.Input(id="disabled", name="disabled", disabled=True, value="omitted"),
            native.Button("Submit", id="submit", type="submit", name="action", value="save"),
            native.Button("Reset", id="reset", type="reset"),
            id="form",
            on_submit=submitted,
            on_reset=reset,
        ),
        native.Input(id="corrected", bind_value=corrected, on_input=normalize),
        native.Button("Program", id="program", on_click=programmatic),
        native.Button("Toggle", id="toggle", on_click=toggle),
        conditional,
        native.Div(text, id="text-state"),
        native.Div(note, id="note-state"),
        native.Div(checked, id="check-state"),
        native.Div(single, id="single-state"),
        native.Div(selected, id="multi-state"),
        native.Div(radio, id="radio-state"),
        native.Div(corrected, id="corrected-state"),
        native.Div(resets, id="reset-state"),
        native.Div(invalids, id="invalid-state"),
        native.Pre(status, id="status"),
        id="forms-fixture",
    )
