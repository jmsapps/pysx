"""Typed styled bases and deterministic flat-declaration inheritance."""

import ast
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from string.templatelib import Template

import pytest

from pysx import Fragment, StyledTag, div, html, render, styled, stylesheet


def test_styled_bases_flattened_opposing_lineages() -> None:
    red = styled(div, t"color: red; padding: 3px")
    blue = styled(div, t"color: blue; padding: 7px")
    forward = styled(red, t"color: blue")
    reverse = styled(blue, t"color: red")
    assert forward.css_class != reverse.css_class
    assert forward.declarations == ("color: red; padding: 3px", "color: blue")
    preregistered = styled(div, Template(";\n".join(forward.declarations)))
    assert preregistered.css_class == forward.css_class
    assert stylesheet().count(f".{forward.css_class} {{") == 1
    assert styled(styled(forward, t"color: blue"), t"padding: 11px").tag == "div"


def test_styled_bases_callable_signature_and_roots() -> None:
    def base(label: str) -> Fragment:
        return html(t'\nfragment:\n  div: {label}\n  span: "other"')

    first = styled(base, t"color: red")
    second = styled(first, t"color: blue")
    result = render(lambda: second("hello"))
    assert result.body.count(f'class="{second.css_class}"') == 2
    assert first.css_class not in result.body
    assert "hello" in result.body


@pytest.mark.parametrize("base", ["div", 5, None])
def test_styled_bases_reject_invalid_runtime(base: object) -> None:
    with pytest.raises(TypeError, match="base"):
        styled(base, t"color: red")  # type: ignore[call-overload]


def test_styled_bases_reject_interpolation_and_blocks() -> None:
    color = "red"

    with pytest.raises(ValueError, match="interpolations"):
        styled(div, t"color: {color}")

    with pytest.raises(ValueError, match="flat"):
        styled(div, Template("div { color: red }"))


def test_styled_bases_no_positive_string_calls() -> None:
    root = Path(__file__).resolve().parents[1]

    for folder in ("examples", "pysx"):
        for path in (root / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "styled"
                    and node.args
                ):
                    assert not isinstance(node.args[0], ast.Constant), str(path)


def test_styled_bases_native_compatibility() -> None:
    result = styled(div, t"color: green")
    assert isinstance(result, StyledTag)
    assert result.tag == "div"


def test_styled_bases_typed_native_callable_children() -> None:
    from pysx import native

    base = styled(native.Strong, t"color: red")

    def app() -> Fragment:
        return html(t'\nBase:\n  span: "child"', namespace={"Base": base})

    result = render(app)
    assert "children=" not in result.body
    assert "<span>child</span>" in result.body
    assert f'<strong class="{base.css_class}"' in result.body


def test_styled_bases_transparent_fragment_and_keyed_roots() -> None:
    from pysx import each, signal
    from pysx.elements import fragment

    transparent = styled(fragment, t"color: orange")
    rows = signal(["one", "two"])

    def item(label: str) -> Fragment:
        return html(t"\nspan: {label}")

    def base() -> Fragment:
        return html(t"\n{each(rows, item, key=lambda row: row)}")

    keyed = styled(base, t"padding: 3px")

    def app() -> Fragment:
        return html(
            t'\nTransparent:\n  span: "one"\n  span: "two"\nKeyed:',
            namespace={"Transparent": transparent, "Keyed": keyed},
        )

    result = render(app)
    assert result.body.count(f'class="{transparent.css_class}"') == 2
    assert result.body.count(f'class="{keyed.css_class}"') == 2


def test_styling_diagnostics_literal_css_and_signal_rejection() -> None:
    from pysx import css, signal

    assert css(t"padding: 3px") == "padding: 3px"

    with pytest.raises(TypeError, match="signals"):
        css(signal("color: red"))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="interpolations"):
        css(t"color: {'red'!r}")

    def app() -> Fragment:
        return html(t'\ndiv(css={"color:red"!s}): "metadata"')

    with pytest.raises(ValueError, match="metadata"):
        render(app)


def test_styling_diagnostics_styled_effect_parity() -> None:
    from pysx import effect, signal
    from pysx.server import Session

    source = signal("red")
    variable = signal("")
    observer = effect(lambda: variable.set(source()))
    base = styled(div, t"color: var(--accent)")
    child = styled(base, t"padding: 3px")

    def app() -> Fragment:
        return html(
            t'\nChild(styleVars={ ({"accent": variable}) }): "Effect"', namespace={"Child": child}
        )

    session = Session(app)

    try:
        source.set("blue")
        op = session.pending[-1]
        assert op["op"] == "attr"
        assert op["name"] == "style"
        assert op["v"] == "--accent:blue"
        assert "padding: 3px" in session.rendered.css
    finally:
        observer.dispose()
        session.dispose()
    assert not source.observers
    assert not variable.observers


def test_styling_diagnostics_fresh_vsix_contents(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    destination = tmp_path / "editor"
    destination.mkdir()

    for name in ("build_vsix.py", "package.json", "extension.js"):
        shutil.copyfile(root / "editor" / name, destination / name)
    shutil.copytree(root / "editor" / "syntaxes", destination / "syntaxes")
    result = subprocess.run(
        [sys.executable, str(destination / "build_vsix.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    archive = Path(result.stdout.strip())
    assert archive.parent == destination

    with zipfile.ZipFile(archive) as package:
        grammar = "syntaxes/pysx.injection.tmLanguage.json"
        assert package.read("extension/" + grammar) == (root / "editor" / grammar).read_bytes()
        assert b"endLine" in package.read("extension/extension.js")
        assert b"support.type.property-name.css" in package.read("extension/" + grammar)


def test_styled_callable_wrapper_outranks_its_styled_base() -> None:
    base = styled(div, t"padding: 4px")

    def body() -> Fragment:
        return html(t'\nBase: "x"')

    wrapper = styled(body, t"padding: 9px")
    result = render(lambda: wrapper(), namespace={"Base": base})
    assert f'class="{base.css_class} {wrapper.css_class}"' in result.body
    assert result.css.index(f".{base.css_class} {{") < result.css.index(f".{wrapper.css_class} {{")
