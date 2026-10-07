"""Settled computations, batched writes and owned structured projections."""

from pysx import (
    Fragment,
    all_of,
    any_of,
    batch,
    component,
    concat,
    contains,
    derived,
    html,
    length,
    not_,
    signal,
)

from .components import (
    Action,
    Actions,
    Card,
    Description,
    Eyebrow,
    Page,
    SecondaryAction,
    Title,
)


@component
def app() -> Fragment:
    count = signal(1)
    doubled = count * 2
    tripled = 3 * count
    total = derived(lambda: doubled() + tripled())
    profile = signal({"scores": {"first": 10, "second": 20}})
    first = profile["scores"]["first"]
    second = profile["scores"]["second"]
    rows = signal([10, 20, 30])
    head = rows[0]
    order = derived(lambda: ", ".join(str(value) for value in rows()))
    above_threshold = count > 2.5
    score_in_rows = contains(rows, first)
    eligible = all_of(above_threshold, score_in_rows)
    either = any_of(above_threshold, score_in_rows)
    blocked = not_(eligible)
    status = concat("First score: ", first)
    size = length(rows)

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

    def reset_count(_event: object) -> None:
        count.set(1)

    return html(
        t"""
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
            Card:
                h2: "Reactive operators"
                p(id="operator-status"): {status}
                p(id="operator-size"): "Rows: " {size}
            Card:
                h2: "Derived boolean helpers"
                Description:
                    "Two conditions: the count is above 2.5 and the first score is in the list."
                p(id="derived-all"): "all_of — both conditions: " {eligible}
                p(id="derived-any"): "any_of — either condition: " {either}
                p(id="derived-not"): "not_ — not both conditions: " {blocked}
                Description:
                    "Advance twice, increment the first score, then reset the count."
                Actions:
                    SecondaryAction(type="button", onClick={reset_count}): "Reset count"
                if {eligible}:
                    p(id="operator-branch"):
                        "The first score is in the list and the count is above 2.5."
    """,
    )
