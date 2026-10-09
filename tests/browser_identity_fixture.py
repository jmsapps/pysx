"""Constrained DOM, foreign content and multiple-root keyed ranges."""

from pysx import Fragment, each, pysx, signal


def app() -> Fragment:
    rows = signal(["alpha", 'quotes"{λ}-->:'])
    live = signal("initial")
    alternate = signal(False)

    def reverse(_event: object) -> None:
        rows.set(list(reversed(rows())))

    def add(_event: object) -> None:
        rows.set([*rows(), "new"])
        live.set("updated")
        alternate.set(True)

    def remove(_event: object) -> None:
        rows.set(rows()[:-1])

    def table_row(value: str) -> Fragment:
        return pysx(t"""
          tr(data-row={value}):
            td: {value}
        """)

    def option_row(value: str) -> Fragment:
        return pysx(t"""option(value={value}): {value}""")

    def svg_row(value: str) -> Fragment:
        return pysx(t"""
          g(data-row={value}):
            text: {value}
        """)

    def math_row(value: str) -> Fragment:
        return pysx(t"""mi(data-row={value}): {value}""")

    def fragment(value: str) -> Fragment:
        return pysx(t"""
            span(data-part={value}): {value}
            input(data-control={value}, value={value})
            x-row(data-custom={value}): "custom"
        """)

    return pysx(t"""
        button(id="reverse", onClick={reverse}): "Reverse"
        button(id="add", onClick={add}): "Add"
        button(id="remove", onClick={remove}): "Remove"
        table:
          tbody(id="rows"): {each(rows, table_row, key=lambda value: value)}
        select(id="options"): {each(rows, option_row, key=lambda value: value)}
        svg(id="drawing"): {each(rows, svg_row, key=lambda value: value)}
        math(id="formula"): {each(rows, math_row, key=lambda value: value)}
        div(id="fragments"): {each(rows, fragment, key=lambda value: value)}
        table:
          tbody:
            tr:
              td(id="live"): {live}
        table:
          tbody(id="branch-rows"):
            if {alternate}:
              tr:
                td(id="branch-cell"): {live}
            else:
              tr:
                td: "initial branch"
    """)
