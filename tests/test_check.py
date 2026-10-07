import tempfile
from pathlib import Path

import pytest

from pysx.check import Diagnostic, diagnostics

HEADER = "from pysx import component, div, html, signal, styled\n\n"


@pytest.mark.parametrize(
    "expression",
    [
        "[value]",
        "(value,)",
        "[str(n) for n in range(value())]",
        "alias",
    ],
)
def test_render_snapshot_freeze_warnings(expression: str) -> None:
    results, _lines = check(
        "value = signal(2)\nparts = [str(n) for n in range(value())]\nalias = parts\n"
        + "html(t'\\ndiv: {"
        + expression
        + "}')\n"
    )
    assert len(results) == 1
    assert results[0]["severity"] == "warning"
    assert "snapshot" in results[0]["message"]


def test_render_snapshot_clean_constants_and_deferred_callbacks() -> None:
    results, _lines = check(
        "from pysx import Binding, defer\n"
        "value = signal(2)\nrow = Binding[int]('row')\n"
        "html(t'\\ndiv: {[1, 2]}')\n"
        "html(t'\\ndiv: {defer(row, lambda n: [value() + n])}')\n"
    )
    assert results == []


def test_positioned_multiline_checker_normalizes_adjacent_empty_fragments() -> None:
    results, _lines = check(
        'number = signal(1.25)\nlabel = "é"\n'
        "view = t'\\np(title={number:.2f}): {number!s}{label!a}'\n"
        "html(view)\n"
    )
    assert results == []


def test_positioned_multiline_checker_assembled_templates_and_metadata() -> None:
    results, _lines = check(
        "head = t'\\nbutton(\\n  onClick={(lambda: None)!r}\\n):'\n"
        "tail = t' \"😀\"\\n'\nhtml(head + tail)\n"
    )
    assert len(results) == 1
    assert "EVENT interpolation" in results[0]["message"]


def test_positioned_multiline_ast_fragments_match_runtime_strings() -> None:
    import ast

    from pysx.check import template_fragments

    left, right = "a", "b"
    template = t"\np: {left}{right}"
    expression = ast.parse("t'\\np: {left}{right}'", mode="eval").body
    assert isinstance(expression, ast.TemplateStr)
    assert template_fragments(expression) == template.strings


def test_styled_authoring_constant_variant_and_removed_call() -> None:
    results, _lines = check(
        'Action = styled.button(t"color: red", variants={"primary": t"color: blue"})\n'
        'html(t\'\\n{Action}(variant="missing"): "click"\')\n'
        'Old = styled(div, t"color: red")\n'
    )
    assert len(results) == 2
    assert any("unknown variant" in item["message"] for item in results)
    assert any("removed" in item["message"] for item in results)


@pytest.mark.parametrize("value", ["42", "None", '"div"', "[]"])
def test_component_tags_constant_unsupported_diagnostics(value: str) -> None:
    results, _lines = check("html(t'\\n{" + value + '}: "child"\')\n')
    assert len(results) == 1
    assert "component tag" in results[0]["message"]


@pytest.mark.parametrize("base", ['"div"', '"d\\x69v"', "None", "42"])
def test_styling_diagnostics_invalid_base_raw_utf16(base: str) -> None:
    results, lines = check(f'note = "😀"; Widget = styled({base})(t"color: red")\n')
    assert len(results) == 1
    result = results[0]
    line = lines[result["line"]].encode("utf-16-le")
    raw = line[result["startChar"] * 2 : result["endChar"] * 2].decode("utf-16-le")
    assert raw == base
    assert "base" in result["message"]


@pytest.mark.parametrize("hole", ["{color}", "{color!r}", "{color:>8}", "{color!s:>8}"])
def test_styling_diagnostics_css_holes_metadata_raw_range(hole: str) -> None:
    results, lines = check(
        f'color = signal("red")\nWidget = styled(div)(t"""\n    /* 😀 */ color: {hole};\n""")\n'
    )
    assert len(results) == 1
    result = results[0]
    line = lines[result["line"]].encode("utf-16-le")
    raw = line[result["startChar"] * 2 : result["endChar"] * 2].decode("utf-16-le")
    assert raw == hole
    assert "styleVars" in result["message"]


def test_styling_diagnostics_multiline_base_range() -> None:
    results, lines = check('Widget = styled("di\\\nv")(t"color: red")\n')
    assert len(results) == 1
    result = results[0]
    assert "endLine" in result
    assert result["endLine"] == result["line"] + 1
    assert lines[result["line"]][result["startChar"] :] == '"di\\'
    assert lines[result["endLine"]][: result["endChar"]] == 'v"'


def test_styling_diagnostics_css_regions_clean_and_global_type() -> None:
    results, _lines = check(
        "from pysx import css, global_style\n"
        'Widget = styled(div)(t"""color: red; --ink: blue; /* comment */""")\n'
        'body = css(t"padding: 3px")\n'
        'global_style("body { color: blue }")\n'
    )
    assert results == []
    results, _lines = check(
        'from pysx import global_style\nglobal_style(t"body {{ color: red }}")\n'
    )
    assert len(results) == 1
    assert "plain string" in results[0]["message"]


def test_styling_diagnostics_css_attribute_metadata() -> None:
    results, lines = check(
        'rules = signal("color:red")\nresult = html(t"""\n    div(css={rules!s}): "sample"\n""")\n'
    )
    assert len(results) == 1
    result = results[0]
    assert lines[result["line"]][result["startChar"] : result["endChar"]] == "{rules!s}"
    assert "metadata" in result["message"]


def test_styling_diagnostics_raw_escapes_and_doubled_braces() -> None:
    results, lines = check(
        'color = "red"\nWidget = styled(div)(t"""\n    /* \\t {{ 😀 */ color: {color!r};\n""")\n'
    )
    assert len(results) == 1
    result = results[0]
    raw = lines[result["line"]].encode("utf-16-le")
    assert raw[result["startChar"] * 2 : result["endChar"] * 2].decode("utf-16-le") == "{color!r}"


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("a and b", "Python and/or coerces Signals; use all_of()/any_of()"),
        ("not a", "Python not coerces a Signal; use not_()"),
        ("0 < a < 3", "Chained comparisons coerce Signals; use all_of(a < b, b < c)"),
        ("a == b", "Signal ==/!= compares identity; use eq()/ne() for reactive payload equality"),
        ("1 in rows", "Python in cannot return a Signal; use contains(container, item)"),
        ("len(rows)", "len(Signal) cannot return a Signal; use length()"),
        ("bool(a)", "bool(Signal) coerces truthiness; use reactive boolean helpers"),
    ],
)
def test_template_ergonomics_exact_coercion_diagnostics(expression: str, message: str) -> None:
    results, lines = check(
        "a = signal(1)\nb = signal(2)\nrows = signal([1])\n"
        f'result = html(t"""\n    p: {{{expression}}}\n""")\n'
    )
    assert len(results) == 1
    warning = results[0]
    assert warning["message"] == message
    assert warning["severity"] == "warning"
    assert (
        lines[warning["line"]][warning["startChar"] : warning["endChar"]] == "{" + expression + "}"
    )


def test_template_ergonomics_supported_and_deferred_forms_clean() -> None:
    results, _ = check(
        "from pysx import all_of, contains, length, eq\n"
        "a = signal(1)\nrows = signal([1])\n"
        'result = html(t"""\n'
        "    p: {a * 2} {a < 3} {length(rows)} {contains(rows, a)}\n"
        "    p: {all_of(a > 0, a < 3)} {eq(a, 1)} {(lambda: a())}\n"
        '""")\n'
    )
    assert results == []


def test_template_ergonomics_projection_alias_snapshot() -> None:
    results, _ = check(
        'state = signal({"a": [1]})\nitem = state["a"][0]\n'
        'result = html(t"""\n    p: {item()}\n""")\n'
    )
    assert len(results) == 1
    assert "frozen" in results[0]["message"]


def check(body: str) -> tuple[list[Diagnostic], list[str]]:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "case.py"
        p.write_text(HEADER + body, encoding="utf-8")

        return diagnostics(p), (HEADER + body).split("\n")


def test_clean_examples_are_empty() -> None:
    here = Path(__file__).resolve().parents[1]

    for name in ("counter", "todos"):
        path = here / f"examples/{name}.py"
        assert diagnostics(path) == [], (name, diagnostics(path))


def test_unknown_tag_after_non_ascii_line() -> None:
    body = (
        'Page = styled(div)(t"""color: red;""")\n'
        'NOTE = "café ☕ — a non-ASCII line before the diagnostic"\n'
        "\n"
        "@component\n"
        "def app():\n"
        '    return html(t"""\n'
        "        Pge:\n"
        '            "hi"\n'
        '    """)\n'
    )
    diags, lines = check(body)
    assert len(diags) == 1, diags
    d = diags[0]
    assert "unknown component 'Pge'" in d["message"], d
    assert lines[d["line"]].strip() == "Pge:", (d, lines[d["line"]])
    assert lines[d["line"]][d["startChar"] : d["endChar"]] == "Pge", d


def test_called_signal_column_is_utf16_on_a_non_ascii_line() -> None:
    body = (
        'Page = styled(div)(t"""color: red;""")\n'
        "\n"
        "@component\n"
        "def app():\n"
        "    count = signal(0)\n"
        '    return html(t"""\n'
        "        Page:\n"
        '            "café ☕ — total: "; {count()}\n'
        '    """)\n'
    )
    diags, lines = check(body)
    warns = [d for d in diags if d["severity"] == "warning"]
    assert len(warns) == 1, diags
    d = warns[0]
    assert "frozen" in d["message"], d
    assert "{count}" in d["message"], d

    line = lines[d["line"]]
    # Slice by UTF-16 units, the way an editor would.
    u16 = line.encode("utf-16-le")
    sliced = u16[d["startChar"] * 2 : d["endChar"] * 2].decode("utf-16-le")
    assert sliced == "{count()}", repr(sliced)
    # And prove the naive byte offset would have been wrong.
    assert d["startChar"] != line.encode("utf-8").index(b"{count()}"), (
        "fixture is not exercising the byte/UTF-16 difference"
    )


def test_bare_lambda_reports_the_parenthesis_fix() -> None:
    body = (
        'Page = styled(div)(t"""color: red;""")\n'
        "\n"
        "@component\n"
        "def app():\n"
        '    return html(t"""\n'
        "        Page(onClick={lambda _e: None}):\n"
        '            "hi"\n'
        '    """)\n'
    )
    diags, _ = check(body)
    assert len(diags) == 1, diags
    assert "wrap it in parentheses" in diags[0]["message"], diags


def test_fstring_instead_of_tstring() -> None:
    body = (
        'Page = styled(div)(t"""color: red;""")\n'
        "\n"
        "@component\n"
        "def app():\n"
        '    return html(f"""\n'
        "        Page:\n"
        '            "hi"\n'
        '    """)\n'
    )
    diags, _ = check(body)
    assert len(diags) == 1, diags
    assert "needs a t-string" in diags[0]["message"], diags


def test_parser_error_is_reported() -> None:
    body = (
        'Page = styled(div)(t"""color: red;""")\n'
        "\n"
        "@component\n"
        "def app():\n"
        '    return html(t"""\n'
        "        Page:\n"
        "            else:\n"
        '                Page: "hi"\n'
        '    """)\n'
    )
    diags, _ = check(body)
    assert any("without a matching" in d["message"] for d in diags), diags
