"""Settled computations, batched writes and owned structured projections."""

from pysx import (
    Fragment,
    batch,
    component,
    derived,
    dict_key,
    html,
    list_index,
    signal,
    structured,
)

from .components import Action as Action
from .components import Actions as Actions
from .components import Card as Card
from .components import Description as Description
from .components import Eyebrow as Eyebrow
from .components import Page as Page
from .components import SecondaryAction as SecondaryAction
from .components import Title as Title


@component
def app() -> Fragment:
    count = signal(1)
    doubled = derived(lambda: count() * 2)
    tripled = derived(lambda: count() * 3)
    total = derived(lambda: doubled() + tripled())
    profile = structured({"scores": {"first": 10, "second": 20}})
    scores = dict_key(profile, "scores")
    first = dict_key(scores, "first")
    second = dict_key(scores, "second")
    rows = structured([10, 20, 30])
    head = list_index(rows, 0)
    order = derived(lambda: ", ".join(str(value) for value in rows()))

    def advance(_event: object) -> None:
        with batch():
            count.set(count() + 1)
            count.set(count() + 1)

    def increment_first(_event: object) -> None:
        first.update(lambda value: value + 1)

    def increment_second(_event: object) -> None:
        second.update(lambda value: value + 1)

    def rotate(_event: object) -> None:
        rows.update(lambda values: [*values[1:], values[0]])

    def edit_snapshot(_event: object) -> None:
        snapshot = profile()
        snapshot["scores"]["first"] = 999

    return html(t"""
        Page:
            header:
                Eyebrow: "pysx / examples"
                Title: "Reactive state"
                Description: "Explore how small changes flow through connected values."
            Card:
                h2: "Batched computation"
                p: "Source: " {count} " | doubled + tripled: " {total}
                Description: "Advance twice in one batch. The connected values update together."
                Actions:
                    Action(type="button", onClick={advance}): "Advance twice"
            Card:
                h2: "Nested write-through"
                p: "First: " {first} " | second: " {second}
                Description:
                    "Each score updates independently. A private snapshot changes neither."
                Actions:
                    Action(type="button", onClick={increment_first}): "Increment first"
                    SecondaryAction(type="button", onClick={increment_second}): "Increment second"
                    SecondaryAction(type="button", onClick={edit_snapshot}):
                        "Edit a private snapshot"
            Card:
                h2: "Positional list projection"
                p: "Order: " {order} " | first position: " {head}
                Description: "Rotate the list to see the first position follow its new value."
                Actions:
                    Action(type="button", onClick={rotate}): "Rotate list"
    """)
