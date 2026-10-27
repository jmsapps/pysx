"""Owned branches, nested lists, stable rows and reusable children."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pysx import (
    Children,
    Dom,
    Fragment,
    each,
    local_state,
    on_cleanup,
    own_effect,
    pysx,
    signal,
)
from tutorial.components import Actions, Button, Field, Panel, Readout

if TYPE_CHECKING:
    from collections.abc import Callable


def branches() -> Fragment:
    visible = signal(True)
    parent = signal(0)
    cleanups = signal(0)

    def Child() -> Fragment:
        count = local_state("child-count", 0)
        on_cleanup(lambda: cleanups.update(lambda value: value + 1))

        def increment(_: object) -> None:
            count.update(lambda value: value + 1)

        return pysx(t"""
            Panel(id="conditional-child")
                Button(id="child-increment" onClick={increment}): "Increment child"
                Readout(id="child-count"): {count}
        """)

    def toggle(_: object) -> None:
        visible.set(not visible())

    def increment(_: object) -> None:
        parent.update(lambda value: value + 1)

    return pysx(t"""
        Actions:
            Button(id="branch-toggle" onClick={toggle}): "Hide or show child"
            Button(id="parent-increment" onClick={increment}): "Increment parent"
        Readout(id="parent-count"): {parent}
        Readout(id="branch-cleanups"): {cleanups}
        if {visible}:
            Child:
        else:
            p(id="child-absent"): "The child owner has been disposed."
    """)


def loops() -> Fragment:
    groups = signal(["Read", "Build"])
    snapshot = len(groups())
    selected = signal("None")

    def group(name: str) -> Fragment:
        def child(label: str) -> Fragment:
            def choose(_: object) -> None:
                selected.set(f"{name} / {label}")

            choice_id = name + "-" + label

            return pysx(t"""
                Button(data-choice={choice_id} onClick={choose}): {label}
            """)

        return pysx(t"""
            li(data-group={name})
                h3: {name}
                Actions: {each(("first", "second"), child, key=str)}
        """)

    def add(_: object) -> None:
        if len(groups()) < 12:
            groups.update(lambda rows: [*rows, f"Group {len(rows) + 1}"])

    def reverse(_: object) -> None:
        groups.update(lambda rows: list(reversed(rows)))

    return pysx(t"""
        Actions:
            Button(id="group-add" onClick={add}): "Add group"
            Button(id="group-reverse" onClick={reverse}): "Reverse groups"
        ul(id="groups"): {each(groups, group, key=str)}
        Readout(id="group-selection"): {selected}
        p: "Snapshot group count: " {snapshot}
    """)


@dataclass(frozen=True)
class Row:
    key: str
    label: str


def keyed_rows() -> Fragment:
    initial = (Row("alpha", "Alpha"), Row("beta", "Beta"))
    rows = signal(list(initial))
    dom = Dom()

    def row(item: Row) -> Fragment:
        draft = local_state("draft", "")
        count = local_state("count", 0)
        ref = dom.ref()

        def increment(_: object) -> None:
            count.update(lambda value: value + 1)

        async def move(_: object) -> None:
            await ref.handle().focus()
            rows.update(lambda values: list(reversed(values)))

        return pysx(t"""
            li(data-row={item.key})
                label(for={"draft-" + item.key}): {item.label}
                Field(id={"draft-" + item.key} ref={ref} bindValue={draft})
                Actions:
                    Button(data-increment={item.key} onClick={increment}): "Count: " {count}
                    Button(data-move={item.key} onClick={move}): "Focus and reverse"
        """)

    def remove(_: object) -> None:
        rows.update(lambda values: [item for item in values if item.key != "alpha"])

    def restore(_: object) -> None:
        if not any(item.key == "alpha" for item in rows()):
            rows.update(lambda values: [initial[0], *values])

    return pysx(t"""
        ul(id="keyed-rows"): {each(rows, row, key=lambda item: item.key)}
        Actions:
            Button(id="row-remove" onClick={remove}): "Remove Alpha"
            Button(id="row-restore" onClick={restore}): "Restore Alpha"
    """)


def components() -> Fragment:
    title = signal("Shared heading")

    def Card(*, number: int, children: Children | None = None) -> Fragment:
        count = local_state("count", 0)

        def increment(_: object) -> None:
            count.update(lambda value: value + 1)

        return pysx(t"""
            Panel(data-instance={number})
                h3: {title}
                {children}
                Button(data-card-increment={number} onClick={increment}): "Increment instance"
                Readout(data-card-count={number}): {count}
        """)

    return pysx(t"""
        label(for="component-title"): "Heading shared by both component instances"
        Field(id="component-title" bindValue={title})
        Card(number={1}): "Caller-owned content for the first card."
        Card(number={2}): "Different children, independent local state."
    """)


def effects() -> Fragment:
    count = signal(0)
    active = signal(True)
    log = signal("")
    entries: list[str] = []

    def record(text: str) -> None:
        entries.append(text)
        del entries[:-10]
        log.set(" | ".join(entries))

    def Observer() -> Fragment:
        def observe() -> Callable[[], None]:
            value = count()
            record(f"run {value}")

            return lambda: record(f"cleanup {value}")

        own_effect("lesson-observer", observe)

        return pysx(t"""
            p(id="observer-active"): "Observer attached"
        """)

    def increment(_: object) -> None:
        count.update(lambda value: value + 1)

    def toggle(_: object) -> None:
        active.set(not active())

    return pysx(t"""
        Actions:
            Button(id="effect-increment" onClick={increment}): "Increment source"
            Button(id="effect-toggle" onClick={toggle}): "Attach or dispose observer"
        Readout(id="effect-source"): {count}
        if {active}:
            Observer:
        Readout(id="effect-log" role="log"): {log}
    """)
