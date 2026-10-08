"""Typed controls, structured writes, normalization and native form transactions."""

import json
from typing import cast

from pysx import Fragment, component, derived, html, signal

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
                Form(id="form" onSubmit={submitted} onReset={reset}):
                    label(htmlFor="text"): "Required text"
                    Field(
                        id="text" name="text" required={True}
                        bindValue={text} onInvalid={invalid}
                    )
                    label(htmlFor="note"): "Notes"
                    textarea(id="note" name="note" bindValue={note} css={field_css})
                    label(htmlFor="check"): "Enable updates"
                    input(id="check" name="check" type="checkbox" value="yes" bindChecked={checked})
                    label(htmlFor="single"): "Single choice"
                    select(id="single" name="single" bindValue={single} css={field_css}):
                        option(value="a"): "A"
                        option(value="b"): "B"
                    label(htmlFor="multi"): "Multiple choices"
                    select(
                        id="multi" name="color" multiple={True} size={2}
                        bindSelected={selected} css={field_css}
                    ):
                        option(value="a"): "A"
                        option(value="b"): "B"
                    label(htmlFor="radio-a"): "Radio A"
                    input(id="radio-a" type="radio" name="radio" value="a" bindValue={radio})
                    label(htmlFor="radio-b"): "Radio B"
                    input(id="radio-b" type="radio" name="radio" value="b" bindValue={radio})
                    label(htmlFor="disabled"): "Disabled field"
                    Field(id="disabled" name="disabled" disabled={True} value="omitted")
                    Submit(id="submit" type="submit" name="action" value="save"): "Submit"
                    SecondaryAction(id="reset" type="reset"): "Reset to initial values"
            Card:
                h2: "Server corrections"
                Description: "This field converts edits to uppercase while keeping your caret."
                div:
                    label(htmlFor="corrected"): "Uppercase correction"
                    Field(id="corrected" bindValue={corrected} onInput={normalize})
                div:
                    Submit(id="program" onClick={programmatic}): "Apply server values"
                    SecondaryAction(id="toggle" onClick={toggle}): "Show or hide optional field"
                if {visible}:
                    label(htmlFor="owned"): "Optional field"
                    Field(id="owned" bindValue={owned})
            Card:
                h2: "Live values"
                dl:
                    dt: "Text"
                    dd(id="text-state"): {text}
                    dt: "Notes"
                    dd(id="note-state"): {note}
                    dt: "Checked"
                    dd(id="check-state"): {checked}
                    dt: "Single choice"
                    dd(id="single-state"): {single}
                    dt: "Multiple choices"
                    dd(id="multi-state"): {selected_text}
                    dt: "Radio choice"
                    dd(id="radio-state"): {radio}
                    dt: "Corrected text"
                    dd(id="corrected-state"): {corrected}
                    dt: "Resets"
                    dd(id="reset-state"): {resets}
                    dt: "Invalid submissions"
                    dd(id="invalid-state"): {invalids}
                h3: "Last submission"
                pre(id="status"): {status}
    """
    )
