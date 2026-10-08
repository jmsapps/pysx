"""Escaped snapshot structure, captured callbacks and live sequence acceptance."""

from pysx import Fragment, pysx, signal, styled

Card = styled.section(t"color: rgb(255, 0, 0)")
Other = styled.aside(t"color: rgb(0, 0, 255)")


def app() -> Fragment:
    value = signal("initial")
    live = signal(["red", "blue"])
    picked = signal("none")

    def change(_event: object) -> None:
        value.set("changed")
        live.set(["<green>"])

    def capture(_event: object) -> None:
        picked.set("snapshot handler")

    entries = [
        pysx(t'\nCard(id="left"): {value}', use=(Card,), namespace={"Card": Card}),
        (pysx(t'\nCard(id="right"): "right"', use=(Other,), namespace={"Card": Other}),),
        t'\nbutton(id="captured",onClick={capture}): "Captured"',
        "<unsafe>&",
    ]

    return pysx(t"""
        button(id="change",onClick={change}): "Change"
        p(id="picked"): {picked}
        div(id="snapshot"): {entries}
        div(id="live"): {live}
    """)
