"""Practice diagnostics and compatible hot reload in your own editor.

pysx-playground development
pysx --app playgrounds.development --watch
"""

from pysx import Fragment, derived, pysx, signal


def app() -> Fragment:
    count = signal(0)
    draft = signal("")
    doubled = derived(lambda: count() * 2)

    def increment(_event: object) -> None:
        count.set(count() + 1)

    return pysx(t"""
        main:
            h1: "Development playground"
            p: "Edit playgrounds/development.py while serving with --watch."
            label(htmlFor="draft"): "Draft to retain across compatible edits"
            input(id="draft" bindValue={draft} maxlength="200")
            p(id="draft-echo"): {draft}
            p:
                "Count: "
                span(id="count"): {count}
            p:
                "Doubled: "
                span(id="doubled" title="A derived value"): {doubled}
            button(id="increment" type="button" onClick={increment}): "Increment"
            h2: "Try it"
            ol:
                li: "Increment the count and enter a draft."
                li: "Change the h1 text in your editor and save. The state stays in this tab."
                li: "Change the count span's live hole from count to count(). Inspect the warning."
                li: "Increment again to see the frozen count, then restore the live count hole."
                li:
                    "Misspell title as tittle on the doubled span, "
                    "inspect the warning, then fix it."
                li: "Save a Python syntax error. The working page remains until you fix the file."
            p: "Use the pysx editor extension with the environment containing pysx[lsp]."
    """)
