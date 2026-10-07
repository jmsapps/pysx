"""Bound components, Python row builders, lazy branches and frozen snapshots."""

from typing import TYPE_CHECKING

from pysx import Fragment, bounded_while, each, each_indexed, eq, html, signal, styled, when

from .components.controls import Action, Actions, Description, Eyebrow, Title
from .components.templates import GroupHeading, GroupPanel, TemplatePage, snapshot_label

if TYPE_CHECKING:
    from collections.abc import Callable

type Group = tuple[str, tuple[str, ...]]


def app() -> Fragment:
    initial: list[Group] = [("work", ("plan", "build")), ("learn", ("read",))]
    groups = signal(initial)
    child_sources = {name: signal(children) for name, children in initial}
    mode = signal("full")
    selected = signal("none")
    serial = signal(0)
    local_heading = styled(GroupHeading)(t"letter-spacing: 0.02em")

    def reverse(_event: object) -> None:
        groups.set(list(reversed(groups())))

    def add(_event: object) -> None:
        serial.update(lambda value: value + 1)
        name = f"extra-{serial()}"
        child_sources[name] = signal(("try",))
        groups.set([*groups(), (name, ("try",))])

    def add_child(_event: object) -> None:
        if not groups():

            return
        source = child_sources[groups()[0][0]]
        source.set((*source(), f"child-{len(source()) + 1}"))

    def remove(_event: object) -> None:
        groups.set(groups()[:-1])

    def cycle(_event: object) -> None:
        mode.set({"full": "compact", "compact": "quiet", "quiet": "full"}[mode()])

    def choose(current: Group, name: str) -> Callable[[object], None]:
        def pick(_event: object) -> None:
            selected.set(f"{current[0]} / {name}")

        return pick

    def group_row(index: int, group: Group) -> Fragment:
        heading = group[0].title()
        heading = f"{index + 1}. {heading}"

        def child_row(child: str) -> Fragment:
            tooltip = "Build something" if child == "build" else None

            return html(t"""
                Action(
                  data-pick={f"{group[0]}:{child}"},
                  title={tooltip},
                  variant={"primary" if child == "build" else "ghost"},
                  onClick={choose(group, child)},
                ): {child}
            """)

        return html(t"""
            GroupPanel(data-group={group[0]}):
              local_heading: {heading}
              Actions:
                {each(child_sources[group[0]], child_row, key=str)}
        """)

    snapshots = [snapshot_label(name) for name, _children in initial]
    frozen_rows = each(initial, lambda group: snapshot_label(group[0]), key=lambda group: group[0])
    lazy_branch = when(
        conditions=[
            (eq(mode, "full"), lambda: html(t'p(id="branch"): "Full details"')),
            (eq(mode, "compact"), lambda: html(t'p(id="branch"): "Compact details"')),
        ],
        default=lambda: html(t'p(id="branch"): "Quiet details"'),
    )
    step = 0

    def build_step() -> Fragment:
        nonlocal step
        step += 1

        return snapshot_label(f"While step {step}")

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
            Action(id="add-child", onClick={add_child}): "Add child to first group"
            Action(id="remove", onClick={remove}): "Remove last group"
            Action(id="cycle", onClick={cycle}): "Switch branch"

          {lazy_branch}

          if {eq(mode, "full")}:
            p(id="optional-branch"): "Full view"
          elif {eq(mode, "compact")}:
            p(id="optional-branch"): "Compact view"
          else:
            p(id="optional-branch"): "Quiet view"
          match {mode}:
            case "full" | "compact":
              p(id="case"): "Editing enabled"
            case _:
              p(id="case"): "Quiet mode"

          p(id="selected"): "Selected: "; {selected}

          {each_indexed(groups, group_row, key=lambda group: group[0])}

          GroupPanel(id="snapshots"):
            GroupHeading: "Snapshots from the initial payload"
            {snapshots}
            {frozen_rows}
            {ordinary}
    """,
    )
