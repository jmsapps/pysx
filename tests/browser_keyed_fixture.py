"""Editable keyed controls and nested ranges with retained local ownership."""

from dataclasses import dataclass, replace

from pysx import (
    BrowserEvent,
    Dom,
    Fragment,
    Signal,
    each,
    local_state,
    on_event,
    on_mount,
    own_task,
    pysx,
    signal,
)


@dataclass(frozen=True)
class Item:
    key: str
    label: str
    area: bool = False


def app() -> Fragment:
    rows = signal([Item("a", "Alpha"), Item("b", "Beta")])
    selected = signal("none")
    listened = signal(0)
    counts: dict[str, int] = {}
    drafts: dict[str, Signal[str]] = {}
    children: dict[str, Signal[list[str]]] = {}

    def reverse(_event: object) -> None:
        rows.set(list(reversed(rows())))

    def edit(_event: object) -> None:
        rows.set([replace(item, label="Current") if item.key == "a" else item for item in rows()])

    def remount(_event: object) -> None:
        rows.set(
            [replace(item, area=not item.area) if item.key == "a" else item for item in rows()]
        )

    def nested(_event: object) -> None:
        source = children["a"]
        source.set([*reversed(source()), str(len(source()) + 1)])

    def remove(_event: object) -> None:
        rows.set([item for item in rows() if item.key != "a"])

    def restore(_event: object) -> None:
        if not any(item.key == "a" for item in rows()):
            rows.set([*rows(), Item("a", "Restored")])

    def clear(_event: object) -> None:
        selected.set("none")

    def row(item: Item) -> Fragment:
        counts[item.key] = counts.get(item.key, 0) + 1
        count = local_state("count", 0)
        draft = local_state("draft", item.label)
        child_rows = local_state("children", ["one", "two"])
        drafts[item.key] = draft
        children[item.key] = child_rows
        ref = Dom().ref()

        def notified(_event: BrowserEvent) -> None:
            listened.set(listened() + 1)

        async def mounted() -> None:
            await ref.handle().listen("custom-keyed", on_event(notified), window=True)

        def mount_listener() -> None:
            own_task("listener", mounted)

        on_mount(mount_listener)

        def increment(_event: object) -> None:
            count.set(count() + 1)

        def pick(_event: object) -> None:
            selected.set(item.label)

        control = (
            pysx(t"textarea(data-control={item.key}, ref={ref}, bindValue={draft})")
            if item.area
            else pysx(t"input(data-control={item.key}, ref={ref}, bindValue={draft})")
        )

        def leaf(value: str) -> Fragment:
            return pysx(t"span(data-child={item.key + ':' + value}): {value}")

        return pysx(t"""
            section(
              data-row={item.key}, data-build={counts[item.key]}, title={item.label}
              role="group", aria-label={item.label}
            ):
              {control}
              button(data-count={item.key}, onClick={increment}): {count}
              button(data-pick={item.key}, onClick={pick}): {item.label}
              div(data-nested={item.key}): {each(child_rows, leaf, key=str)}
              p(data-selection={item.key}): "selection target"
              div(data-scroll={item.key}, style="height: 30px; overflow: auto"):
                div(style="height: 200px"): "Scrollable row"
            footer(data-footer={item.key}): {item.label}
        """)

    return pysx(t"""
        button(id="reverse", onClick={reverse}): "Reverse"
        button(id="edit", onClick={edit}): "Edit label"
        button(id="remount", onClick={remount}): "Remount control"
        button(id="nested", onClick={nested}): "Nested insert/reorder"
        button(id="remove", onClick={remove}): "Remove Alpha"
        button(id="restore", onClick={restore}): "Restore Alpha"
        button(id="clear", onClick={clear}): "Clear selection"
        p(id="selected"): {selected}
        p(id="listened"): {listened}
        div(id="keyed-rows"): {each(rows, row, key=lambda item: item.key)}
    """)
