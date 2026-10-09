"""Editable keyed rows retain local state, focus, refs and nested children."""

from dataclasses import dataclass, replace

from pysx import Dom, Fragment, each, local_state, pysx, signal

from .components import (
    Action,
    Actions,
    Card,
    Description,
    Eyebrow,
    Field,
    Meta,
    Page,
    SecondaryAction,
    Title,
)


@dataclass(frozen=True)
class Entry:
    key: str
    label: str


def app() -> Fragment:
    rows = signal([Entry("alpha", "Alpha"), Entry("beta", "Beta")])
    chosen = signal("none")
    dom = Dom()

    def reverse(_event: object) -> None:
        rows.set(list(reversed(rows())))

    def rename(_event: object) -> None:
        rows.set(
            [
                replace(item, label="Alpha renamed") if item.key == "alpha" else item
                for item in rows()
            ]
        )

    def remove(_event: object) -> None:
        rows.set([item for item in rows() if item.key != "alpha"])

    def restore(_event: object) -> None:
        if not any(item.key == "alpha" for item in rows()):
            rows.set([*rows(), Entry("alpha", "Alpha restored")])

    def row(item: Entry) -> Fragment:
        draft = local_state("draft", item.label)
        count = local_state("count", 0)
        children = local_state("children", ["one", "two"])
        ref = dom.ref()
        field_id = "draft-" + item.key

        def increment(_event: object) -> None:
            count.update(lambda value: value + 1)

        def pick(_event: object) -> None:
            chosen.set(item.label)

        def add_child(_event: object) -> None:
            children.set([*reversed(children()), str(len(children()) + 1)])

        async def focus_and_reverse(_event: object) -> None:
            await ref.handle().focus()
            rows.set(list(reversed(rows())))

        def child(value: str) -> Fragment:
            child_id = item.key + ":" + value

            def choose(_event: object) -> None:
                chosen.set(f"{item.label} / {value}")

            return pysx(t"SecondaryAction(data-child={child_id}, onClick={choose}): {value}")

        return pysx(t"""
            Card(data-row={item.key}, role="group", aria-label={item.label}, title={item.label}):
              h2: {item.label}
              label(for={field_id}): "Private draft"
              Field(id={field_id}, ref={ref}, bindValue={draft})
              Actions:
                Action(data-count={item.key}, onClick={increment}): "Count: " {count}
                SecondaryAction(data-pick={item.key}, onClick={pick}): "Pick " {item.label}
                SecondaryAction(
                  data-focus-reverse={item.key}, onClick={focus_and_reverse}
                ): "Focus draft and reverse"
                SecondaryAction(
                  data-add={item.key}, onClick={add_child}
                ): "Add and reverse children"
              Actions(aria-label="Nested choices"): {each(children, child, key=str)}
            Meta(data-draft={item.key}): "Draft preview: " {draft}
        """)

    return pysx(t"""
        Page:
          Eyebrow: "Keyed identity"
          Title: "Edits stay with their row"
          Description:
            "Type a draft and increase its count. "
            "Reverse rows or change Alpha's label: your edits stay. "
            "Focus draft and reverse keeps keyboard focus in that field. "
            "Nested choices use the current label. "
            "Remove and restore Alpha to start a fresh row."
          Actions:
            Action(id="keyed-reverse", onClick={reverse}): "Reverse rows"
            SecondaryAction(id="keyed-rename", onClick={rename}): "Rename Alpha"
            SecondaryAction(id="keyed-remove", onClick={remove}): "Remove Alpha"
            SecondaryAction(id="keyed-restore", onClick={restore}): "Restore Alpha"
          Meta(id="keyed-chosen", role="status"): {chosen}
          div(id="keyed-example-rows"): {each(rows, row, key=lambda item: item.key)}
    """)
