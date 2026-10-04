from pysx import Fragment, component, html, signal

from .components import Action as Action
from .components import Actions as Actions
from .components import Description as Description
from .components import Eyebrow as Eyebrow
from .components import Metric as Metric
from .components import Page as Page
from .components import Title as Title
from .components import Value as Value


@component
def app() -> Fragment:
    # Created per session. A module-scope signal would be shared by every
    # connected client.
    count = signal(0)

    def increment(_e: object) -> None:
        count.set(count() + 1)

    return html(t"""
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
    """)
