"""Advisory schema warnings do not prevent rendering or live interactions."""

from pysx import Fragment, pysx, signal


def app() -> Fragment:
    count = signal(0)

    def increment(_event: object) -> None:
        count.update(lambda current: current + 1)

    return pysx(t"""
        section(id="advisory", hreff="preserved"):
            p(id="total"): {count}
            button(id="increment", onClick={increment}): "Increment"
    """)
