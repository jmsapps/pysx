"""Reactive CSS ownership, custom properties and isolated named themes."""

import re
from string.templatelib import Template

import pytest

from pysx import Fragment, Themes, batch, div, each, global_style, html, render, signal, styled
from pysx.server import Session
from pysx.styles import css_name, variable_name, variable_values


def handler(session: Session, identifier: str) -> str:
    match = re.search(rf'id="{identifier}"[^>]*data-pysx-click="([^"]+)"', session.rendered.body)
    assert match is not None

    return match[1]


def test_reactive_css_sessions_themes_and_styles() -> None:
    def app() -> Fragment:
        themes = Themes()
        themes.register("light", {" ink ": "red", "padding": "3px"})
        themes.register("dark", {"--ink": "blue"})
        themes.select("light")
        color = signal("green")
        css = signal("color: var(--local, var(--ink)); padding-left: var(--padding, 0px)")
        classes = signal("first")

        def change(_event: object) -> None:
            themes.select("dark")
            color.set("")
            css.set("color: var(--ink); padding-left: 7px")
            classes.set("second")

        return html(
            t"""
                div(id="styled" css={css} class={classes} styleVars={ ({"local": color}) }): "hello"
                button(id="change" onClick={change}): "Change"
            """,
            themes=themes,
        )

    session, other = Session(app), Session(app)

    try:
        before = other.rendered.css
        assert "--local:green" in session.rendered.body
        assert "--ink:red" in session.rendered.css
        ops = session.dispatch(handler(session, "change"), None)
        assert any(op["op"] == "css" and "--ink:blue" in op["v"] for op in ops)
        assert any(op["op"] == "attr" and op["name"] == "style" and op["v"] is None for op in ops)
        assert any(
            op["op"] == "attr" and op["name"] == "class" and "second" in (op["v"] or "")
            for op in ops
        )
        assert other.rendered.css == before
        assert other.pending == []
        assert "--padding:unset" in session.rendered.css
        assert session.rendered.css.count("color: var(--ink); padding-left: 7px") == 1
    finally:
        session.dispose()
        other.dispose()
    assert not session.rendered.styles.rules


def test_reactive_css_branch_cleanup_and_shared_rule_counts() -> None:
    visible = signal(True)
    css = signal("color: red")
    variable = signal("4px")

    def app() -> Fragment:

        return html(
            t"""
                div(css="color: red"): "retained"
                if {visible}:
                    div(css={css} styleVars={ ({"gap": variable}) }): "owned"
            """
        )

    session = Session(app)

    try:
        assert session.rendered.css.count(f".{css_name('color: red')} ") == 1
        assert len(css.observers) == 1
        assert len(variable.observers) == 1
        visible.set(False)
        assert len(css.observers) == 0
        assert len(variable.observers) == 0
        assert session.rendered.css.count(f".{css_name('color: red')} ") == 1
        assert all("branch:" not in owner for owner in session.rendered.styles.rules)
        css.set("color: blue")
        assert "color: blue" not in session.rendered.css
        visible.set(True)
        assert "color: blue" in session.rendered.css
        assert session.pending[0]["op"] in {"html", "css"}
    finally:
        session.dispose()
    assert not css.observers
    assert not variable.observers


def test_reactive_css_rows_cleanup_and_order() -> None:
    rows = signal(["red", "blue"])

    def item(color: str) -> Fragment:

        return html(t"\ndiv(css={'color: ' + color}): {color}")

    def app() -> Fragment:

        return html(t"\nsection: {each(rows, item, key=lambda row: row)}")

    session = Session(app)

    try:
        assert "color: blue" in session.rendered.css
        rows.set(["red"])
        assert "color: blue" not in session.rendered.css
        assert [op["op"] for op in session.pending] == ["css", "list"]
        assert len(session.rendered.styles.rules) == 1
    finally:
        session.dispose()


def test_reactive_css_runtime_definitions_do_not_enter_shared_registry() -> None:
    from pysx import stylesheet

    before = stylesheet()

    def app() -> Fragment:
        local = styled(div)(Template("color: rgb(12, 34, 56)"))
        global_style("body { background: rgb(65, 43, 21) }")

        return html(t'\nLocal: "private"', namespace={"Local": local})

    first = Session(app)

    def other_app() -> Fragment:

        return html(t'\ndiv: "other"')

    other = Session(other_app)

    try:
        assert "rgb(12, 34, 56)" in first.rendered.css
        assert "rgb(12, 34, 56)" not in other.rendered.css
        assert "rgb(65, 43, 21)" not in other.rendered.css
        assert stylesheet() == before
    finally:
        first.dispose()
        other.dispose()


def test_reactive_css_theme_register_read_clear_and_normalization() -> None:
    themes = Themes()
    theme = themes.register(" first ", {" color ": "red", "-space": "8px", "": "skip"})
    assert themes.read("first") == theme
    themes.select(theme)
    assert themes.current() == theme
    assert "--space:8px" in themes.css()
    themes.clear()
    assert "--color:unset" in themes.css()

    with pytest.raises(ValueError, match="hyphens"):
        variable_name("---invalid")

    with pytest.raises(ValueError, match="invalid"):
        variable_name("a;b")

    with pytest.raises(TypeError, match="strings"):
        variable_values({"value": 3})


def test_reactive_css_style_merge_and_literal_deduplication() -> None:
    value = signal("2px")
    style = signal("color:red")

    def app() -> Fragment:

        return html(
            t"""
                div(css={t"padding: 1px"} style={style} styleVars={ ({"gap": value}) }): "one"
                div(css="padding: 1px"): "two"
            """
        )

    session = Session(app)

    try:
        assert session.rendered.css.count("padding: 1px") == 1
        value.set("")
        op = session.pending[-1]
        assert op["op"] == "attr"
        assert op["v"] == "color:red"
        style.set("color:blue")
        op = session.pending[-1]
        assert op["op"] == "attr"
        assert op["v"] == "color:blue"
    finally:
        session.dispose()


def test_reactive_css_stylesheet_precedes_class_in_reverse_update_order() -> None:
    classes = signal("first")
    css = signal("color: red")

    def app() -> Fragment:

        return html(t'\ndiv(class={classes} css={css}): "ordered"')

    session = Session(app)

    try:
        with batch():
            classes.set("second")
            css.set("color: blue")
        assert session.pending[0]["op"] == "css"
        assert sum(op["op"] == "css" for op in session.pending) == 1
        assert session.rendered.css.count("color: blue") == 1
    finally:
        session.dispose()


def test_reactive_css_preconstructed_callable_fragment_retains_its_rule() -> None:
    def base() -> Fragment:

        return html(t'\ndiv: "prebuilt"')

    styled_base = styled(base)(t"color: rgb(13, 24, 35)")
    prebuilt = styled_base()

    def app() -> Fragment:

        return html(t"\nsection: {prebuilt}")

    session = Session(app)

    try:
        assert f'class="{styled_base.css_class}"' in session.rendered.body
        assert "color: rgb(13, 24, 35)" in session.rendered.css
    finally:
        session.dispose()


def test_reactive_css_typed_native_authoring() -> None:
    from pysx import native

    value = signal("red")

    def app() -> Fragment:

        return native.Div("native", css=t"color: var(--ink)", style_vars={"ink": value})

    session = Session(app)

    try:
        assert "--ink:red" in session.rendered.body
        value.set("blue")
        op = session.pending[-1]
        assert op["op"] == "attr"
        assert op["v"] == "--ink:blue"
    finally:
        session.dispose()


def test_runtime_css_outranks_the_styled_class_on_the_same_element() -> None:
    card = styled(div)(t"padding: 4px")

    def app() -> Fragment:

        return html(t'\nCard(css="padding: 11px"): "x"', namespace={"Card": card})

    result = render(app)
    runtime = css_name("padding: 11px")
    assert f'class="{card.css_class} {runtime}"' in result.body
    assert result.css.index(f".{card.css_class} {{") < result.css.index(f".{runtime} {{")
