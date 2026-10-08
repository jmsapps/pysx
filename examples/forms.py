"""Typed controls, structured writes, normalization and native form transactions."""

import json
from typing import cast

from pysx import Fragment, component, derived, html, native, signal

from .components import (
    Card,
    Description,
    Eyebrow,
    Field,
    Form,
    Page,
    SecondaryAction,
    Submit,
    Title,
)


@component
def app() -> Fragment:
    profile = signal({"text": ["initial"]})
    text = profile["text"][0]
    note = signal("note")
    checked = signal(False)
    single = signal("a")
    selected = signal(["a"])
    selected_text = derived(lambda: repr(selected()))
    radio = signal("a")
    corrected = signal("ABC")
    status = signal("ready")
    resets = signal(0)
    invalids = signal(0)
    visible = signal(True)
    owned = signal("owned")
    field_css = "\n".join(Field.declarations)
    form_css = "\n".join(Form.declarations)
    submit_css = "\n".join(Submit.declarations)
    secondary_css = "\n".join(SecondaryAction.declarations)

    def normalize(_value: object) -> None:
        corrected.set(corrected().upper())

    def submitted(value: object) -> None:
        status.set(json.dumps(cast("dict[str, object]", value), indent=2, sort_keys=True))

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

    controls = native.Form(
        native.Label("Required text", html_for="text"),
        native.Input(
            id="text",
            name="text",
            required=True,
            bind_value=text,
            on_invalid=invalid,
            css=field_css,
        ),
        native.Label("Notes", html_for="note"),
        native.Textarea(id="note", name="note", bind_value=note, css=field_css),
        native.Label("Enable updates", html_for="check"),
        native.Input(id="check", name="check", type="checkbox", value="yes", bind_checked=checked),
        native.Label("Single choice", html_for="single"),
        native.Select(
            native.Option("A", value="a"),
            native.Option("B", value="b"),
            id="single",
            name="single",
            bind_value=single,
            css=field_css,
        ),
        native.Label("Multiple choices", html_for="multi"),
        native.Select(
            native.Option("A", value="a"),
            native.Option("B", value="b"),
            id="multi",
            name="color",
            multiple=True,
            size=2,
            bind_selected=selected,
            css=field_css,
        ),
        native.Label("Radio A", html_for="radio-a"),
        native.Input(id="radio-a", type="radio", name="radio", value="a", bind_value=radio),
        native.Label("Radio B", html_for="radio-b"),
        native.Input(id="radio-b", type="radio", name="radio", value="b", bind_value=radio),
        native.Label("Disabled field", html_for="disabled"),
        native.Input(
            id="disabled",
            name="disabled",
            disabled=True,
            value="omitted",
            css=field_css,
        ),
        native.Button(
            "Submit",
            id="submit",
            type="submit",
            name="action",
            value="save",
            css=submit_css,
        ),
        native.Button(
            "Reset to initial values",
            id="reset",
            type="reset",
            css=secondary_css,
        ),
        id="form",
        on_submit=submitted,
        on_reset=reset,
        css=form_css,
    )
    correction = native.Div(
        native.Label("Uppercase correction", html_for="corrected"),
        native.Input(id="corrected", bind_value=corrected, on_input=normalize, css=field_css),
    )
    actions = native.Div(
        native.Button("Apply server values", id="program", on_click=programmatic, css=submit_css),
        native.Button(
            "Show or hide optional field",
            id="toggle",
            on_click=toggle,
            css=secondary_css,
        ),
    )
    conditional = html(t"""
        if {visible}:
            label(htmlFor="owned"): "Optional field"
            input(id="owned" bindValue={owned} css={field_css})
    """)
    readings = native.Dl(
        native.Dt("Text"),
        native.Dd(text, id="text-state"),
        native.Dt("Notes"),
        native.Dd(note, id="note-state"),
        native.Dt("Checked"),
        native.Dd(checked, id="check-state"),
        native.Dt("Single choice"),
        native.Dd(single, id="single-state"),
        native.Dt("Multiple choices"),
        native.Dd(selected_text, id="multi-state"),
        native.Dt("Radio choice"),
        native.Dd(radio, id="radio-state"),
        native.Dt("Corrected text"),
        native.Dd(corrected, id="corrected-state"),
        native.Dt("Resets"),
        native.Dd(resets, id="reset-state"),
        native.Dt("Invalid submissions"),
        native.Dd(invalids, id="invalid-state"),
    )

    return html(
        t"""
        Page(id="live-forms"):
            header:
                Eyebrow: "pysx / examples"
                Title: "Live forms"
                Description: "Edit controls to see their state update below."
            Card:
                h2: "Native controls"
                Description: "Submit collects enabled fields. Reset restores the initial values."
                {controls}
            Card:
                h2: "Server corrections"
                Description: "This field converts edits to uppercase while keeping your caret."
                {correction}
                {actions}
                {conditional}
            Card:
                h2: "Live values"
                {readings}
                h3: "Last submission"
                pre(id="status"): {status}
    """
    )
