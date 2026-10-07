"""Session theme/variable/rule changes exercised by three browser engines."""

from pysx import Fragment, Themes, html, signal


def app() -> Fragment:
    themes = Themes()
    themes.register("light", {"ink": "red"})
    themes.register("dark", {"ink": "blue"})
    themes.select("light")
    variable = signal("green")
    css = signal("color: var(--local, var(--ink)); padding-left: 3px")
    classes = signal("literal")
    visible = signal(True)

    def change(_event: object) -> None:
        themes.select("dark")
        variable.set("")
        css.set("color: var(--local, var(--ink)); padding-left: 7px")
        classes.set("dynamic")

    def toggle(_event: object) -> None:
        visible.set(not visible())

    def clear(_event: object) -> None:
        themes.clear()

    return html(
        t"""
        div(id="sample" css={css} class={classes} styleVars={ ({"local": variable}) }): "Sample"
        if {visible}:
            div(id="owned" css="margin-left: 37px"): "Owned"
        button(id="change" onClick={change}): "Change"
        button(id="toggle" onClick={toggle}): "Toggle"
        button(id="clear" onClick={clear}): "Clear"
    """,
        themes=themes,
    )
