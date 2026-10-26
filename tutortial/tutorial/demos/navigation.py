"""Real course-scoped navigation, accessible chooser and variable-driven preview."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pysx import BrowserEvent, Dom, Fragment, Link, derived, native, on_event, pysx, signal, when
from tutorial.components import Actions, Button, Field, Panel, Readout
from tutorial.themes import selected_name

if TYPE_CHECKING:
    from pysx import NavigationEvent, Router, RouteState, Themes


def styles(themes: Themes) -> Fragment:
    spacing = signal("16px")
    draft = signal("")
    variables = {"preview-space": spacing}
    name = derived(lambda: selected_name(themes))

    def compact(_: object) -> None:
        spacing.set("8px" if spacing() == "16px" else "16px")

    def switch(_: object) -> None:
        themes.select("dark" if name() == "light" else "light")

    return pysx(t"""
        Actions:
            Button(id="preview-spacing" onClick={compact}): "Change spacing"
            Button(id="preview-theme" onClick={switch}): "Switch preview theme"
        Panel(id="style-preview" styleVars={variables}
            style="padding: var(--preview-space)")
            label(for="style-draft"): "Keep this input while changing presentation"
            Field(id="style-draft" bindValue={draft})
        Readout(id="preview-theme-name"): {name}
    """)


def routes(router: Router, slug: str, state: RouteState) -> Fragment:
    path = "/learn/" + slug
    location = derived(lambda: router.location().url)
    notice = signal("No cancelled navigation")
    project = derived(lambda: state.params().get("id", "No project selected"))
    visits = signal(0)

    def increment(_: object) -> None:
        visits.update(lambda value: value + 1)

    def cancel(event: NavigationEvent) -> None:
        event.cancel_navigation()
        notice.set("Navigation cancelled")

    return pysx(t"""
        Actions:
            Link(router={router} href={path + "/projects/1"} id="route-one"): "Project one"
            Link(router={router} href={path + "/projects/2"} id="route-two"): "Project two"
            Link(router={router} href="?tab=details" replace={True} id="route-query"):
                "Replace query"
            Link(router={router} href={path} id="route-reset"): "Lesson root"
            Link(router={router} href={path + "/files/docs/start"} id="route-files"):
                "Wildcard files"
            Link(router={router} href={path + "/not-a-project"} id="route-missing"):
                "Lesson fallback"
            Link(router={router} href={path + "/cancelled"} on_navigate={cancel}
                id="route-cancel"): "Cancel this navigation"
            Link(router={router} href={path} target="_blank" id="route-native"):
                "Open lesson in another tab"
        Readout(id="route-location"): {location}
        Readout(id="route-project"): {project}
        Button(id="route-increment" onClick={increment}): "Increment route-local count"
        Readout(id="route-count"): {visits}
        Readout(id="route-notice"): {notice}
    """)


def keyboard() -> Fragment:
    dom = Dom()
    ref = dom.ref()
    active = signal(0)
    opened = signal(False)
    selected = signal("None")
    labels = ("Read", "Build", "Ship")
    descendant = derived(lambda: f"choice-{active()}")

    def key(event: BrowserEvent) -> None:
        if event.key in ("ArrowDown", "ArrowUp"):
            active.set((active() + (1 if event.key == "ArrowDown" else -1)) % len(labels))
            opened.set(True)
        elif event.key == "Enter":
            selected.set(labels[active()])
            opened.set(False)
        elif event.key == "Escape":
            opened.set(False)

    async def focus(_: object) -> None:
        await ref.handle().focus()

    key_handler = on_event(
        key, keys=("ArrowDown", "ArrowUp", "Enter", "Escape"), prevent_default=True
    )
    tree = tree_controls()

    return pysx(t"""
        label(for="chooser"): "Choose a project stage"
        Field(id="chooser" role="combobox" ref={ref} ariaControls="choices"
            ariaExpanded={opened} ariaActivedescendant={descendant}
            onKeydown={key_handler})
        ul(id="choices" role="listbox" hidden={derived(lambda: not opened())})
            li(id="choice-0" role="option" ariaSelected={derived(lambda: active() == 0)}): "Read"
            li(id="choice-1" role="option" ariaSelected={derived(lambda: active() == 1)}): "Build"
            li(id="choice-2" role="option" ariaSelected={derived(lambda: active() == 2)}): "Ship"
        Button(id="focus-chooser" onClick={focus}): "Focus chooser"
        Readout(id="chooser-selection"): {selected}
        h3: "A recursive tree with one tab stop"
        {tree}
    """)


@dataclass(frozen=True)
class TreeEntry:
    key: str
    label: str
    children: tuple[TreeEntry, ...] = ()


def tree_controls() -> Fragment:
    root = TreeEntry("root", "Projects", (TreeEntry("read", "Read"), TreeEntry("build", "Build")))
    dom = Dom()
    refs = {key: dom.ref() for key in ("root", "read", "build")}
    opened = signal(False)
    active = signal("root")
    chosen = signal("")
    labels = {"root": "Projects", "read": "Read", "build": "Build", "": "None"}
    selection = derived(lambda: labels[chosen()])

    def node(entry: TreeEntry, level: int = 1) -> Fragment:
        async def keyboard_event(event: BrowserEvent) -> None:
            shown = ["root", "read", "build"] if opened() else ["root"]
            index = shown.index(entry.key)
            destination = entry.key

            if event.key == "ArrowRight" and entry.children:
                if opened():
                    destination = entry.children[0].key
                else:
                    opened.set(True)
            elif event.key == "ArrowLeft":
                if entry.key == "root":
                    opened.set(False)
                destination = "root"
            elif event.key in ("ArrowDown", "ArrowUp"):
                destination = shown[
                    max(0, min(len(shown) - 1, index + (1 if event.key == "ArrowDown" else -1)))
                ]
            elif event.key in ("Home", "End"):
                destination = shown[0 if event.key == "Home" else -1]
            elif event.key == "Enter":
                chosen.set(entry.key)
            active.set(destination)
            await refs[destination].handle().focus()

        children = (
            when(
                conditions=(
                    (
                        opened,
                        lambda: native.Ul(
                            *(node(child, level + 1) for child in entry.children), role="group"
                        ),
                    ),
                )
            )
            if entry.children
            else native.Fragment()
        )

        return native.Li(
            native.Span(entry.label),
            children,
            id="lesson-tree-" + entry.key,
            ref=refs[entry.key],
            role="treeitem",
            tabindex=derived(lambda: 0 if active() == entry.key else -1),
            custom_attrs={
                "aria-expanded": opened if entry.children else None,
                "aria-level": level,
                "aria-selected": derived(lambda: chosen() == entry.key),
            },
            on_keydown=on_event(
                keyboard_event,
                keys=("ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End", "Enter"),
                prevent_default=True,
                stop_propagation=True,
            ),
        )

    return native.Fragment(
        native.Ul(
            node(root),
            id="lesson-tree",
            role="tree",
            custom_attrs={"aria-label": "Project learning tree"},
        ),
        native.Output(selection, id="tree-selection"),
    )
