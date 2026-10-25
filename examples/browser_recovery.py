"""An ordinary draft form that stays with its open browser document."""

from pysx import Fragment, pysx, signal

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
    field_css = "\n".join(Field.declarations)

    def revise(_event: object) -> None:
        revisions.set(revisions() + 1)

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
    """)


example = example_app(app)
