"""An ordinary draft form that stays with its open browser document."""

import asyncio

from pysx import Fragment, each, pysx, signal

from .components import (
    Action,
    Card,
    Description,
    Eyebrow,
    Field,
    Meta,
    Page,
    Title,
    example_app,
    example_title,
)


def app() -> Fragment:
    example_title("Browser recovery")
    name = signal("")
    notes = signal("")
    revisions = signal(0)
    history = signal(list[int]())
    status = signal("Ready")
    field_css = "\n".join(Field.declarations)

    def revise(_event: object) -> None:
        revisions.set(revisions() + 1)
        history.set([*history(), revisions()])

    def reverse(_event: object) -> None:
        history.set(list(reversed(history())))

    async def check(_event: object) -> None:
        status.set("Checking")
        await asyncio.sleep(0.1)
        status.set("Checked")

    def revision(value: int) -> Fragment:
        return pysx(t"li(data-revision={value}): {value}")

    return pysx(t"""
        Page(id="browser-recovery"):
            header:
                Eyebrow: "pysx / examples"
                Title: "A draft that stays with this tab"
                Description: "Restart the server with this tab open. Reloading starts fresh."
            Card:
                label(htmlFor="recovery-name"): "Name"
                Field(id="recovery-name" name="name" bindValue={name} autocomplete="off")
                label(htmlFor="recovery-notes"): "Notes"
                textarea(id="recovery-notes" bindValue={notes} rows="4" css={field_css})
                Action(id="add-revision" type="button" onClick={revise}): "Add a revision"
                Meta:
                    "Revision: "
                    span(id="recovery-revision"): {revisions}
                Action(id="reverse-revisions" type="button" onClick={reverse}): "Reverse revisions"
                ol(id="recovery-history"): {each(history, revision, key=lambda value: value)}
                Action(id="check-draft" type="button" onClick={check}): "Check draft"
                Meta(id="draft-status" ariaLive="polite"): {status}
    """)


example = example_app(app)
