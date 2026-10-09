"""Recursive tree controls with callable children and component ownership."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pysx import (
    BrowserEvent,
    Dom,
    Fragment,
    batch,
    each,
    local_state,
    native,
    on_cleanup,
    on_event,
    on_mount,
    on_setup,
    pysx,
    signal,
    styled,
)

from .controls import Action

if TYPE_CHECKING:
    from collections.abc import Awaitable


Panel = styled.section(t"""
    padding: 16px;
    border: 1px solid var(--border);
    border-radius: 12px;
    &:hover:
      border-color: var(--accent);
    @media (max-width: 600px):
      padding: 12px;
""")
TreePanel = styled(Panel)(t"background: var(--surface);")


@dataclass(frozen=True)
class Entry:
    key: str
    label: str
    children: tuple[Entry, ...] = ()


ROOTS = (
    Entry(
        "library",
        "Library",
        (
            Entry("components", "Components", (Entry("signals", "Signals"),)),
            Entry("lifecycle", "Lifecycle"),
        ),
    ),
    Entry("notes", "Notes"),
)


def tree_controls() -> Fragment:
    dom = Dom()
    root_ref = dom.ref()
    roots = signal(list(ROOTS))
    setups = signal(0)
    mounts = signal(0)
    cleanups = signal(0)
    entries: dict[str, Entry] = {}
    parents: dict[str, str] = {}
    levels: dict[str, int] = {}

    def collect(nodes: tuple[Entry, ...], parent: str = "", level: int = 1) -> None:
        for node in nodes:
            entries[node.key] = node
            parents[node.key] = parent
            levels[node.key] = level
            collect(node.children, node.key, level + 1)

    collect(ROOTS)
    expanded = {key: signal(False) for key in entries}
    tabs = {key: signal(0 if key == "library" else -1) for key in entries}
    sources = {key: signal(list(node.children)) for key, node in entries.items()}

    def visible(nodes: list[Entry]) -> list[str]:
        found: list[str] = []

        for node in nodes:
            found.append(node.key)

            if expanded[node.key]():
                found.extend(visible(list(node.children)))

        return found

    def select(key: str) -> None:
        with batch():
            for identifier, tab in tabs.items():
                tab.set(0 if identifier == key else -1)

    async def focus(key: str) -> None:
        select(key)
        target = await root_ref.handle().get_by_id(f"tree-{key}")

        if target is not None:
            await target.focus()

    def branch(*, node: Entry) -> Fragment:
        visits = local_state("visits", 0)
        opened = expanded[node.key]
        on_setup(lambda: setups.set(setups() + 1))
        on_cleanup(lambda: cleanups.set(cleanups() + 1))
        on_mount(lambda: mounts.set(mounts() + 1))

        async def keyboard(event: BrowserEvent) -> None:
            shown = visible(roots())
            index = shown.index(node.key)
            destination = node.key

            if event.key == "ArrowDown":
                destination = shown[min(index + 1, len(shown) - 1)]
            elif event.key == "ArrowUp":
                destination = shown[max(index - 1, 0)]
            elif event.key == "Home":
                destination = shown[0]
            elif event.key == "End":
                destination = shown[-1]
            elif event.key == "ArrowRight" and node.children:
                if opened():
                    destination = node.children[0].key
                else:
                    opened.set(True)
            elif event.key == "ArrowLeft":
                if node.children and opened():
                    opened.set(False)
                elif parents[node.key]:
                    destination = parents[node.key]
            elif event.key in {"Enter", " "}:
                visits.set(visits() + 1)

                if node.children:
                    opened.set(not opened())
            await focus(destination)

        def clicked(_event: BrowserEvent) -> Awaitable[None]:
            select(node.key)
            visits.set(visits() + 1)

            if node.children:
                opened.set(not opened())

            return focus(node.key)

        def focused(_event: BrowserEvent) -> None:
            select(node.key)

        children = each(sources[node.key], row, key=lambda child: child.key)
        aria_expanded = opened if node.children else None
        keys = ("ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End", "Enter", " ")

        group = pysx(t"""
            if {opened}:
                ul(role="group"): {children}
        """)

        return native.Li(
            native.Span(node.label, " · activations: ", visits),
            group,
            id=f"tree-{node.key}",
            role="treeitem",
            tabindex=tabs[node.key],
            custom_attrs={"aria-level": levels[node.key], "aria-expanded": aria_expanded},
            on_keydown=on_event(keyboard, keys=keys, prevent_default=True, stop_propagation=True),
            on_click=on_event(clicked, stop_propagation=True),
            on_focus=on_event(focused, stop_propagation=True),
        )

    def row(node: Entry) -> Fragment:
        return pysx(t"\nbranch(node={node}):")

    def reverse(_event: BrowserEvent) -> None:
        roots.set(list(reversed(roots())))

    return pysx(
        t"""
            TreePanel:
                ul(id="composition-tree",role="tree",aria-label="Component library",ref={root_ref}):
                    {each(roots, row, key=lambda node: node.key)}
                p(id="tree-setups"):
                    "Setups: "; {setups}
                p(id="tree-mounts"): "Browser mounts: "; {mounts}
                p(id="tree-cleanups"): "Cleanups: "; {cleanups}
                br:
                Action(id="tree-reverse", onClick={on_event(reverse)}): "Reverse roots"
        """,
    )
