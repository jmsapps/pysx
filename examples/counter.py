from pysx import Fragment, component, html, signal

from .components import (
    Action,
    Actions,
    Description,
    Eyebrow,
    Metric,
    Page,
    Title,
    Value,
)


@component
def app() -> Fragment:
    # Created per session. A module-scope signal would be shared by every
    # connected client.
    count = signal(0)

    def increment(_e: object) -> None:
        count.set(count() + 1)

    return html(
        t"""
        Page(id="container"):
            header:
                Eyebrow: "pysx / examples"
                Title: "Counter"
                Description: "A simple counter. Each click adds one to the total."
            Metric:
                "Count: "
                Value: {count}
            Actions:
                Action(type="button", onClick={increment}): "Increment"
    """,
        use=(Action, Actions, Description, Eyebrow, Metric, Page, Title, Value),
    )
