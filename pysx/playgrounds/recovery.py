"""An ordinary draft that stays with its open browser document across a restart.

Scaffold this playground, then run it:

    pysx --playground recovery
    pysx --app playgrounds.recovery
"""

from pysx import Fragment, each, pysx, signal


def app() -> Fragment:
    name = signal("")
    notes = signal("")
    revisions = signal(0)
    history = signal(list[int]())

    def revise(_event: object) -> None:
        revisions.set(revisions() + 1)
        history.set([*history(), revisions()])

    def reverse(_event: object) -> None:
        history.set(list(reversed(history())))

    def revision(value: int) -> Fragment:
        return pysx(t"li(data-revision={value}): {value}")

    return pysx(t"""
        main:
            h1: "Browser-held recovery"
            p: "Ordinary local state participates in the host's default recovery policy."
            label(htmlFor="name"): "Name"
            input(id="name" bindValue={name} autocomplete="off")
            label(htmlFor="notes"): "Notes"
            textarea(id="notes" bindValue={notes} rows="4")
            p:
                "Revision: "
                span(id="revision"): {revisions}
            button(id="revise" type="button" onClick={revise}): "Add a revision"
            button(id="reverse" type="button" onClick={reverse}): "Reverse revisions"
            ol(id="history"): {each(history, revision, key=lambda value: value)}
            h2: "Try it"
            ol:
                li: "Fill in the draft and add a few revisions."
                li: "Stop the server with Ctrl+C, leaving this tab open."
                li: "Start the same command again. The open document restores your draft."
                li: "Refresh the page instead, and it starts fresh."
    """)
