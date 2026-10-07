"""Session-isolated branches, nested lexical loops and captured events."""

from typing import TYPE_CHECKING

from pysx import Binding, Fragment, defer, eq, html, signal

if TYPE_CHECKING:
    from collections.abc import Callable


def app() -> Fragment:
    rows = signal(["alpha", "beta"])
    mode = signal("first")
    selected = signal("none")
    row = Binding[str]("row")
    child = Binding[str]("child")

    def reverse(_event: object) -> None:
        rows.set(list(reversed(rows())))

    def remove(_event: object) -> None:
        rows.set(rows()[:-1])

    def toggle(_event: object) -> None:
        mode.set("second" if mode() == "first" else "last")

    def capture(value: str) -> Callable[[object], None]:
        def click(_event: object) -> None:
            selected.set(value)

        return click

    return html(
        t"""
        button(id="reverse", onClick={reverse}): "Reverse"
        button(id="remove", onClick={remove}): "Remove"
        button(id="toggle", onClick={toggle}): "Branch"
        p(id="selected"): {selected}
        if {eq(mode, "first")}:
          p(id="branch"): "first"
        elif {eq(mode, "second")}:
          p(id="branch"): "second"
        else:
          p(id="branch"): "last"
        match {mode}:
          case "first" | "second":
            span(id="case"): "pair"
          case _:
            span(id="case"): "other"
        for row in {rows} key={row}:
          section(data-row={row}):
            button(data-pick={row}, onClick={defer(row, capture)}): {row}
            for child in {defer(row, lambda value: [value + "-1", value + "-2"])} key={child}:
              span(data-child={child}): {child}
    """,
        namespace={"row": row, "child": child},
    )
