"""Live branches, lexical nested rows and ordinary Python snapshots."""

from typing import TYPE_CHECKING

from pysx import Binding, Fragment, bounded_while, defer, defer2, derived, eq, html, signal

from .components.controls import Action, Actions, Description, Eyebrow, Title
from .components.templates import GroupHeading, GroupPanel, SnapshotLabel, TemplatePage

if TYPE_CHECKING:
    from collections.abc import Callable

type Group = tuple[str, tuple[str, ...]]


def app() -> Fragment:
    initial: list[Group] = [("work", ("plan", "build")), ("learn", ("read",))]
    groups = signal(initial)
    indexed = derived(lambda: list(enumerate(groups())))
    mode = signal("full")
    selected = signal("none")
    serial = signal(0)
    index = Binding[int]("index")
    group = Binding[Group]("group")
    child = Binding[str]("child")
    heading = Binding[str]("heading")
    tooltip = defer(child, lambda name: "Build something" if name == "build" else None)

    def reverse(_event: object) -> None:
        groups.set(list(reversed(groups())))

    def add(_event: object) -> None:
        serial.update(lambda value: value + 1)
        groups.set([*groups(), (f"extra-{serial()}", ("try",))])

    def remove(_event: object) -> None:
        groups.set(groups()[:-1])

    def cycle(_event: object) -> None:
        mode.set({"full": "compact", "compact": "quiet", "quiet": "full"}[mode()])

    def choose(current: Group, name: str) -> Callable[[object], None]:
        def pick(_event: object) -> None:
            selected.set(f"{current[0]} / {name}")

        return pick

    snapshots = [
        html(t"\nSnapshotLabel: {name}", use=(SnapshotLabel,)) for name, _children in initial
    ]
    step = 0

    def build_step() -> Fragment:
        nonlocal step
        step += 1

        return html(t"\nSnapshotLabel: {f'While step {step}'}", use=(SnapshotLabel,))

    ordinary = bounded_while(lambda: step < 2, build_step, limit=2)

    return html(
        t"""
        TemplatePage(id="templates-example"):
          Eyebrow: "Template language"
          Title: "Live rows and snapshots"
          Description: "Reorder groups or pick a row. Snapshots stay fixed."
          Actions:
            Action(id="reverse", onClick={reverse}): "Reverse groups"
            Action(id="add", onClick={add}): "Add group"
            Action(id="remove", onClick={remove}): "Remove last group"
            Action(id="cycle", onClick={cycle}): "Switch branch"
          if {eq(mode, "full")}:
            p(id="branch"): "Full details"
          elif {eq(mode, "compact")}:
            p(id="branch"): "Compact details"
          else:
            p(id="branch"): "Quiet details"
          match {mode}:
            case "full" | "compact":
              p(id="case"): "Editing enabled"
            case _:
              p(id="case"): "Quiet mode"
          p(id="selected"): "Selected: "; {selected}
          for (index, group) in {indexed} key={defer(group, lambda current: current[0])}:
            let heading = {defer(group, lambda current: current[0].title())}
            set heading = {defer2(index, heading, lambda i, name: f"{i + 1}. {name}")}
            GroupPanel(data-group={defer(group, lambda current: current[0])}):
              GroupHeading: {heading}
              Actions:
                for child in {defer(group, lambda current: current[1])} key={child}:
                  Action(
                    data-pick={defer2(group, child, lambda current, name: f"{current[0]}:{name}")},
                    title={tooltip},
                    variant={defer(child, lambda name: "primary" if name == "build" else "ghost")},
                    onClick={defer2(group, child, choose)},
                  ): {child}
          GroupPanel(id="snapshots"):
            GroupHeading: "Snapshots from the initial payload"
            {snapshots}
            {ordinary}
    """,
        use=(
            TemplatePage,
            Eyebrow,
            Title,
            Description,
            Actions,
            Action,
            GroupPanel,
            GroupHeading,
            SnapshotLabel,
        ),
        namespace={"index": index, "group": group, "child": child, "heading": heading},
    )
