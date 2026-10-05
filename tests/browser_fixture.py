"""Per-session native control fixture for browser verification."""

from pysx import Fragment, html, signal


def app() -> Fragment:
    value = signal('a<&"')
    checked = signal(False)
    selected = signal(False)
    hidden = signal(False)
    classes = signal("first")

    def change(_event: object) -> None:
        value.set("server")
        checked.set(True)
        selected.set(True)
        hidden.set(True)
        classes.set('first second "quoted"')

    return html(t"""
        div(id="serialization")
            label(htmlFor="text" className="label"): "Text"
            input(id="text" value={value} maxLength={20} tabIndex={2} readOnly={False})
            input(id="checked" type="checkbox" checked={checked})
            textarea(id="area" value={value})
            select(id="select" value={"b"})
                option(value="a"): "A"
                option(value="b"): "B"
            select(id="selected")
                option(value="a"): "A"
                option(id="option-b" value="b" selected={selected}): "B"
            div(id="hidden" hidden={hidden} aria-hidden={checked}): "hide"
            my-widget(id="custom" dataValue={value} class="first" className={classes})
            svg(id="vector" viewBox="0 0 10 10")
                circle(cx="5" cy="5" r="2")
                foreignObject()
                    div(id="foreign"): "HTML"
            math(id="math")
                mi(): "x"
            button(id="properties" onClick={change}): "Properties"
    """)
