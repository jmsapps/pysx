"""A reactive counter with an explicitly authored development state panel.

Run `pysx --app examples.development:app --watch` from the repository.
Follow the on-page steps to test hot updates and fresh page loads.
"""

from pathlib import Path

from pysx import Application, Fragment, StaticAsset, component, pysx, signal

from .components import Action, Actions, Card, Description, Eyebrow, Page, Title, Value
from .components.development import state_panel


@component
def view() -> Fragment:
    count = signal(0)

    def increment(_event: object) -> None:
        count.set(count() + 1)

    return pysx(t"""
        Page:
            header:
                Eyebrow: "pysx / examples"
                Title: "Hot reload"
                Description: "Save an edit to update this page while keeping its local count."
            Card:
                p:
                    "Count: "
                    Value(id="development-count"): {count}
                Actions:
                    Action(id="development-increment", type="button", onClick={increment}):
                        "Increment"
            Card:
                h2: "Try a hot update"
                ol:
                    li: "Start at 0 and increment to 1."
                    li: 'Edit the title in examples/development.py and save. The count stays 1.'
                    li: "Increment again to 2, then refresh the page. The count returns to 0."
                    li: "Open a new tab: its count also starts at 0."
                Description:
                    "Separately, change count = signal(0) to signal(1) and save. "
                    "The initializer change resets the count to 1. Restore signal(0) afterward."
            {state_panel(count)}
            script(src="/development-panel.js", defer={True})
    """)


app = Application(view)
app.assets["/development-panel.js"] = StaticAsset.from_path(
    Path(__file__).parent / "components" / "development.js", content_type="text/javascript"
)
