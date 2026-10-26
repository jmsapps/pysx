"""Large keyed form plus parser-constrained namespace fixtures."""

from pysx import Fragment, each, pysx, signal


def app() -> Fragment:
    order = signal(list(range(1000)))
    fields = [signal(f"Row {index}") for index in range(1000)]

    def reverse(_event: object) -> None:
        order.set(list(reversed(order())))

    def row(index: int) -> Fragment:
        return pysx(t"""
            li(data-row={str(index)}):
                input(data-field={str(index)}, bindValue={fields[index]})
                span: {fields[index]}
        """)

    return pysx(t"""
        button(id="reverse", onClick={reverse}): "Reverse"
        ul(id="rows"): {each(order, row, key=str)}
        table:
            tbody:
                tr:
                    td: "cell"
        select:
            option(value="one"): "one"
        svg:
            circle(cx="10", cy="10", r="5")
        math:
            mi: "x"
    """)
