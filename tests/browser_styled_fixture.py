"""Production styled cascade and callable roots for browser assertions."""

from pysx import Children, Fragment, div, html, signal, styled


def app() -> Fragment:
    classes = signal("literal")
    red = styled(div, t"color: red; background-color: white; padding-left: 3px")
    blue = styled(div, t"color: blue; background-color: black; padding-left: 7px")
    styled(
        div,
        t"color: blue; background-color: black; padding-left: 7px; color: red; padding-left: 13px",
    )
    red_blue = styled(red, t"color: blue; padding-left: 11px")
    blue_red = styled(blue, t"color: red; padding-left: 13px")
    repeated = styled(red_blue, t"color: blue; padding-left: 11px")
    third = styled(repeated, t"padding-left: 17px")

    def base(*, children: Children, title: str) -> Fragment:
        return html(
            t'\nfragment:\n  div(id="callable"): {title}; {children}\n  span(id="second"): "second"'
        )

    callable_base = styled(base, t"color: red; padding-left: 2px")
    callable_child = styled(callable_base, t"color: blue; padding-left: 19px")

    def change(_event: object) -> None:
        classes.set("dynamic")

    return html(
        t"""
        RedBlue(id="red-blue" class={classes}): "A"
        BlueRed(id="blue-red"): "B"
        Third(id="third"): "C"
        Callable(title="root"):
            strong: "child"
        button(id="change" onClick={change}): "Change"
    """,
        namespace={
            "RedBlue": red_blue,
            "BlueRed": blue_red,
            "Third": third,
            "Callable": callable_child,
        },
    )
