import tempfile
from pathlib import Path

import pytest

from pysx.compiler.source_map import EditorPosition, Positions
from pysx.tooling.check import Diagnostic, apply_fix, diagnostics, diagnostics_for_source

HEADER = "from pysx import div, pysx, signal, styled\n\n"


@pytest.mark.parametrize(
    ("markup", "code", "selected"),
    [
        ("div(hreff='bad')", "native-attribute", "hreff"),
        ("span(open={True})", "native-attribute", "open"),
        ("input(disabled={'bad'})", "native-value", "{'bad'}"),
        ("input(disabled='bad')", "native-value", "'bad'"),
        ("input(disabled={1})", "native-value", "{1}"),
        ("p(title={42})", "native-value", "{42}"),
        ("button(onClick={42})", "event-handler", "{42}"),
        ("input(bindValue={'bad'})", "binding-value", "{'bad'}"),
        ("Panel", "component-required-prop", "Panel"),
        ("Panel(title={42})", "component-prop-value", "{42}"),
        ("Panel(title={'ok'}, extra={1})", "component-unknown-prop", "Panel"),
        ("Panel(title={'ok'}): 'child'", "component-unknown-prop", "Panel"),
    ],
)
def test_diagnostics_actions_advisory_exact_ranges(markup: str, code: str, selected: str) -> None:
    source = (
        "from pysx import Fragment, pysx\n"
        "def Panel(*, title: str) -> Fragment:\n    return pysx(t'p: {title}')\n"
        + 'emoji = "😀"; view = pysx(t"'
        + markup
        + '")\n'
    )
    findings = diagnostics_for_source(source)
    assert len(findings) == 1, findings
    finding = findings[0]
    assert finding["code"] == code
    assert finding["severity"] == "warning"
    positions = Positions(source)
    start = positions.editor_offset(EditorPosition(finding["line"], finding["startChar"]))
    end = positions.editor_offset(
        EditorPosition(finding.get("endLine", finding["line"]), finding["endChar"])
    )
    assert source[start:end] == selected


@pytest.mark.parametrize("literal", ['f"p: {count}"', 't"p: {count()}"'])
def test_diagnostics_actions_guarded_fix_and_reanalysis(literal: str) -> None:
    source = "from pysx import pysx, signal\ncount = signal(0)\n" + f"view = pysx({literal})\n"
    findings = diagnostics_for_source(source)
    assert len(findings) == 1
    fixes = findings[0].get("fixes", [])
    assert len(fixes) == 1
    repaired = apply_fix(source, fixes[0])
    assert diagnostics_for_source(repaired) == []

    with pytest.raises(ValueError, match="stale"):
        apply_fix("😀" + source, fixes[0])


def test_diagnostics_survive_ragged_implicit_concatenation() -> None:
    source = (
        "from pysx import pysx\n"
        "def view():\n"
        '    return pysx(f"<div>"\n'
        '            f"<p>hi</p>"\n'
        '          f"</div>")\n'
    )
    compile(source, "<ragged>", "exec")
    findings = diagnostics_for_source(source)
    assert [finding["code"] for finding in findings] == ["template-fstring"]
    assert findings[0].get("fixes", []) == []


@pytest.mark.parametrize(
    "markup",
    [
        "svg(viewBox='0 0 10 10')",
        "svg(xmlns='http://www.w3.org/2000/svg')",
        "math(display='block')",
        "div(onCustom={handler})",
        "textarea(value='text')",
        "select(value='b')",
        "a(name='anchor')",
    ],
)
def test_diagnostics_accept_foreign_custom_and_control_attributes(markup: str) -> None:
    source = (
        "from pysx import pysx\n"
        "def handler(event: object) -> None: ...\n"
        'view = pysx(t"' + markup + '")\n'
    )
    assert diagnostics_for_source(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "view = pysx(t",
        "def app():\n",
        "😀 =",
        "view = (",
        "from pysx import pysx\nview = pysx(t'input(title=')",
    ],
)
def test_diagnostics_actions_partial_buffer_recovery(source: str) -> None:
    findings = diagnostics_for_source(source)
    assert findings
    positions = Positions(source)

    for finding in findings:
        start = positions.editor_offset(EditorPosition(finding["line"], finding["startChar"]))
        end = positions.editor_offset(
            EditorPosition(finding.get("endLine", finding["line"]), finding["endChar"])
        )
        assert 0 <= start < end <= len(source)
        assert finding["code"]
    assert diagnostics_for_source("from pysx import pysx\nview = pysx(t'input')\n") == []


def test_diagnostics_actions_aliases_defaults_dynamic_and_styled() -> None:
    source = '''from pysx import Fragment, pysx
from pysx.styling.styled import styled as decorate
from functools import partial
def panel(*, title: str = "Default", **props: object) -> Fragment:
    return pysx(t"p: {title} {props}")
Partial = partial(panel, title="fixed")
Card = decorate(Partial)(t"color: red")
class Dynamic: pass
str = Dynamic
def other(*, title: str) -> Fragment:
    return pysx(t"p: {title}")
def app(unknown, factory):
    Widget = factory()
    return pysx(t"""
        Card(variant={'any'}, title={unknown}, extra={1}); Widget(foo={42}); other(title={42})
    """)
'''
    assert diagnostics_for_source(source) == []


def test_diagnostics_actions_styling_identity_and_unrelated_names() -> None:
    source = """from pysx.styling.styled import styled as decorate
Card = decorate('div')(t"color: red")
def styled(*args): return args
unrelated = styled('text', t"value: {42}")
"""
    findings = diagnostics_for_source(source)
    assert len(findings) == 1
    assert findings[0]["code"] == "style-declaration"


def test_diagnostics_actions_shadowed_signal_is_unknown() -> None:
    source = """from pysx import pysx, signal
count = signal(0)
def app(count):
    return pysx(t"p: {count()}")
"""
    assert diagnostics_for_source(source) == []


@pytest.mark.parametrize(
    "markup",
    [
        "div(bindValue={value})",
        "input(bindChecked={value})",
        "select(bindSelected={value})",
        "input(type='radio', bindValue={value})",
    ],
)
def test_diagnostics_actions_binding_control_contract(markup: str) -> None:
    source = (
        "from pysx import pysx, signal\nvalue = signal('draft')\n" + f'view = pysx(t"{markup}")\n'
    )
    findings = diagnostics_for_source(source)
    assert [item["code"] for item in findings] == ["binding-control"]


def test_diagnostics_actions_formatted_and_dynamic_values_stay_conservative() -> None:
    source = """from pysx import pysx, signal
draft = signal('text')
def app(unknown):
    return pysx(t"p(title={42!s}); input(type={unknown}, bindValue={draft}); p(title={unknown})")
"""
    assert diagnostics_for_source(source) == []


def test_diagnostics_actions_assembled_fstring_has_no_guessed_fix() -> None:
    source = """from pysx import pysx
view = pysx(f"p: " f'{42}')
"""
    findings = diagnostics_for_source(source)
    assert findings
    assert not any(item.get("fixes") for item in findings)


def test_diagnostics_actions_marker_alias_and_local_template_scope() -> None:
    source = """from pysx import pysx as html, signal as state
def app():
    count = state(0)
    template = t"p: {count()}"
    return html(template=template)
def other():
    template = t"p: 'different scope'"
    return html(template)
"""
    findings = diagnostics_for_source(source)
    assert len(findings) == 1
    assert findings[0]["code"] == "template-freeze"
    fixes = findings[0].get("fixes", [])
    assert len(fixes) == 1
    fix = fixes[0]
    assert diagnostics_for_source(apply_fix(source, fix)) == []


def test_diagnostics_actions_unrelated_template_spelling_is_ignored() -> None:
    source = """def pysx(value): return value
view = pysx(t"not markup at all")
"""
    assert diagnostics_for_source(source) == []


def test_diagnostics_actions_native_spelling_remains_reserved() -> None:
    source = """from pysx import pysx, styled
button = styled.div(t"color: red")
view = pysx(t"button(disabled={True})")
"""
    assert diagnostics_for_source(source) == []


def test_diagnostics_actions_custom_metadata_and_pseudo_values_are_supported() -> None:
    source = """from pysx import pysx
view = pysx(t"div(data-count={1}, aria-hidden={False}, key={42})")
"""
    assert diagnostics_for_source(source) == []


def test_diagnostics_actions_callable_binding_named_prop_is_not_a_form() -> None:
    source = '''from pysx import Binding, Fragment, pysx, signal
row = Binding[str]('row')
rows = signal(['one'])
value = signal('draft')
def Panel(*, bindValue: object) -> Fragment:
    return pysx(t"p: {bindValue}")
view = pysx(t"""
    for row in {rows}:
        Panel(bindValue={value})
""")
'''
    assert diagnostics_for_source(source) == []


def test_diagnostics_actions_browser_fixture_has_the_advisory_warning() -> None:
    findings = diagnostics(Path(__file__).with_name("browser_diagnostics_fixture.py"))
    assert [item["code"] for item in findings] == ["native-attribute"]


def test_diagnostics_actions_crlf_fixes_match_buffer_and_cli(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    source = (
        'from pysx import pysx, signal\r\ncount = signal(0)\r\nview = pysx(t"p: {count()}")\r\n'
    )
    path = tmp_path / "source.py"
    path.write_bytes(source.encode())
    findings = diagnostics_for_source(source, str(path))
    assert diagnostics(path) == findings
    result = subprocess.run(
        [sys.executable, "-m", "pysx.tooling.check", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == findings
    fixes = findings[0].get("fixes", [])
    assert len(fixes) == 1
    repaired = apply_fix(source, fixes[0])
    assert repaired.count("\r\n") == source.count("\r\n")
    assert diagnostics_for_source(repaired) == []


def test_diagnostics_actions_crlf_eof_uses_valid_editor_line_end() -> None:
    source = "def app():\r\n"
    findings = diagnostics_for_source(source)
    assert len(findings) == 1
    finding = findings[0]
    assert (finding["line"], finding["startChar"]) == (0, len("def app():"))
    assert (finding.get("endLine"), finding["endChar"]) == (1, 0)
    positions = Positions(source)
    assert positions.editor_offset(EditorPosition(0, finding["startChar"])) == source.index("\r")


def test_diagnostics_actions_workspace_signatures_use_unsaved_source_without_execution(
    tmp_path: Path,
) -> None:
    producer = tmp_path / "producer.py"
    saved = "def Panel(*, title: str): return title\nraise AssertionError('must not execute')\n"
    producer.write_text(saved)
    filename = str(tmp_path / "view.py")
    source = (
        'from pysx import pysx\nfrom producer import Panel\nview = pysx(t"Panel(title={42})")\n'
    )
    assert [item["code"] for item in diagnostics_for_source(source, filename)] == [
        "component-prop-value"
    ]
    assert (
        diagnostics_for_source(
            source, filename, buffers={str(producer): saved.replace("title: str", "title: int")}
        )
        == []
    )


def test_diagnostics_actions_unkeyed_form_warning() -> None:
    source = '''from pysx import Binding, pysx, signal
row = Binding[str]('row')
rows = signal(['one'])
value = signal('draft')
view = pysx(t"""
    for row in {rows}:
        input(bindValue={value})
""")
'''
    findings = diagnostics_for_source(source)
    assert [item["code"] for item in findings] == ["form-loop-key"]
    keyed = source.replace("in {rows}:", "in {rows} key={row}:")
    assert diagnostics_for_source(keyed) == []


@pytest.mark.parametrize(
    ("body", "selected", "message"),
    [
        (r"""view = pysx(t"p: '😀 {{brace}} \t'; Missing")""", "Missing", "unknown component"),
        (r"""view = pysx(rt"p: '😀 {{brace}} \t'; Missing")""", "Missing", "unknown component"),
        (r"""view = pysx(t"\n\tdiv:")""", r"\t", "indentation uses spaces"),
        ('view = pysx(t"""\r\n\tdiv:\r\n""")', "\t", "indentation uses spaces"),
        (r"""view = pysx(t"\n  div:\n    p(\n      title=bad\n    )")""", "b", "expected a string"),
        (r"""view = pysx(t"\n  div:\n    for nope:\n      p")""", ":", "expected 'in'"),
        (r"""view = pysx(t"div(")""", '"', "unclosed '('"),
        (
            'head = t"\\n  div:\\n"\ntail = t"    p(title=bad)"\nview = pysx(head + tail)',
            "b",
            "expected a string",
        ),
    ],
)
def test_exact_source_structural_buffer_and_cli_ranges(
    tmp_path: Path, body: str, selected: str, message: str
) -> None:
    import json
    import subprocess
    import sys

    source = "from pysx import pysx\n" + body + "\n"
    path = tmp_path / "source.py"
    path.write_bytes(source.encode())
    findings = diagnostics_for_source(source, str(path))
    assert len(findings) == 1
    finding = findings[0]
    assert message in finding["message"]
    positions = Positions(source)
    start = positions.editor_offset(EditorPosition(finding["line"], finding["startChar"]))
    end = positions.editor_offset(
        EditorPosition(finding.get("endLine", finding["line"]), finding["endChar"])
    )
    assert source[start:end] == selected
    assert end > start
    result = subprocess.run(
        [sys.executable, "-m", "pysx.tooling.check", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == findings


@pytest.mark.parametrize(
    ("row", "message", "selected"),
    [
        (r"p: '😀 {{brace}} \t'; br; Missing", "unknown component", "Missing"),
        (r"p: '😀 {{brace}} \t'; br; input(title=bad)", "expected a string", "b"),
        (r"p: '😀 {{brace}} \t'; br; Card:", "content-free", "C"),
    ],
)
def test_inline_sibling_diagnostic_raw_utf16_ranges(row: str, message: str, selected: str) -> None:
    source = 'from pysx import pysx\nview = pysx(t"' + row + '")\n'
    findings = [item for item in diagnostics_for_source(source) if message in item["message"]]
    assert len(findings) == 1
    finding = findings[0]
    line = source.splitlines()[finding["line"]]
    raw = line.encode("utf-16-le")[finding["startChar"] * 2 : finding["endChar"] * 2].decode(
        "utf-16-le"
    )
    assert raw == selected


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
        + "pysx(t'\\ndiv: {"
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
        "pysx(t'\\ndiv: {[1, 2]}')\n"
        "pysx(t'\\ndiv: {defer(row, lambda n: [value() + n])}')\n"
    )
    assert results == []


def test_positioned_multiline_checker_normalizes_adjacent_empty_fragments() -> None:
    results, _lines = check(
        'number = signal(1.25)\nlabel = "é"\n'
        "view = t'\\np(title={number:.2f}): {number!s}{label!a}'\n"
        "pysx(view)\n"
    )
    assert results == []


def test_positioned_multiline_checker_assembled_templates_and_metadata() -> None:
    results, _lines = check(
        "head = t'\\nbutton(\\n  onClick={(lambda: None)!r}\\n):'\n"
        "tail = t' \"😀\"\\n'\npysx(head + tail)\n"
    )
    assert len(results) == 1
    assert "EVENT interpolation" in results[0]["message"]


def test_positioned_multiline_ast_fragments_match_runtime_strings() -> None:
    import ast

    from pysx.tooling.check import template_fragments

    left, right = "a", "b"
    template = t"\np: {left}{right}"
    expression = ast.parse("t'\\np: {left}{right}'", mode="eval").body
    assert isinstance(expression, ast.TemplateStr)
    assert template_fragments(expression) == template.strings


def test_styled_authoring_constant_variant_and_removed_call() -> None:
    results, _lines = check(
        'Action = styled.button(t"color: red", variants={"primary": t"color: blue"})\n'
        'pysx(t\'\\n{Action}(variant="missing"): "click"\')\n'
        'Old = styled(div, t"color: red")\n'
    )
    assert len(results) == 2
    assert any("unknown variant" in item["message"] for item in results)
    assert any("removed" in item["message"] for item in results)


def test_variant_inheritance_uses_workspace_scopes_and_imports(tmp_path: Path) -> None:
    (tmp_path / "base.py").write_text(
        "from pysx import styled\n"
        'Heading = styled.h1(t"color: red", variants={"big": t"font-size: 2em"})\n'
    )
    (tmp_path / "exports.py").write_text("from base import Heading as Header\n")
    source = """from pysx import styled, pysx
from exports import Header
Local = styled(Header)(t"color: blue", variants={"small": t"font-size: 1em"})
view = pysx(t'Local(variant="big"): "valid"')
invalid = pysx(t'Local(variant="wrong"): "bad"')
def local():
    Local = styled.p(t"color: green", variants={"other": t"color: black"})
    return pysx(t'Local(variant="other"): "valid"')
"""
    findings = diagnostics_for_source(
        source, str(tmp_path / "view.py"), workspace_roots=(tmp_path,)
    )
    variants = [item for item in findings if "unknown variant" in item["message"]]
    assert len(variants) == 1
    assert "wrong" in variants[0]["message"]
    assert "['big', 'small']" in variants[0]["message"]


def test_dynamic_variant_base_does_not_claim_an_empty_variant_set() -> None:
    source = """from pysx import pysx, styled
def decorate(base):
    Local = styled(base)(t"color: red")
    return pysx(t'Local(variant="big"): "valid if inherited"')
"""
    assert not any("unknown variant" in item["message"] for item in diagnostics_for_source(source))


@pytest.mark.parametrize("module", ["pysx", "pysx.styling.styled"])
def test_variant_metadata_recognizes_styled_import_aliases(module: str) -> None:
    source = f"""from {module} import styled as style
from pysx import pysx
Base = style.h1(t"color: red", variants={{"big": t"font-size: 2em"}})
Local = style(Base)(t"color: blue")
view = pysx(t'Local(variant="missing"): "bad"')
"""
    findings = [
        item for item in diagnostics_for_source(source) if "unknown variant" in item["message"]
    ]
    assert len(findings) == 1
    assert "['big']" in findings[0]["message"]


@pytest.mark.parametrize("literal", ['"wrong"', '"wr\\u006fng"'])
def test_literal_variant_error_maps_only_its_raw_value(literal: str) -> None:
    source = f"""from pysx import styled, pysx
Heading = styled.h1(t"color: red", variants={{"big": t"font-size: 2em"}})
view = pysx(t\"\"\"
    Heading(title="😀", variant={literal}): "hello"
\"\"\")
"""
    findings = [
        item for item in diagnostics_for_source(source) if "unknown variant" in item["message"]
    ]
    assert len(findings) == 1
    diagnostic = findings[0]
    assert diagnostic["line"] == diagnostic.get("endLine") == 3
    line = source.splitlines()[3]
    selected = line.encode("utf-16-le")[
        diagnostic["startChar"] * 2 : diagnostic["endChar"] * 2
    ].decode("utf-16-le")
    assert selected == literal


@pytest.mark.parametrize("value", ["42", "None", '"div"', "[]"])
def test_component_tags_constant_unsupported_diagnostics(value: str) -> None:
    results, _lines = check("pysx(t'\\n{" + value + '}: "child"\')\n')
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
        'rules = signal("color:red")\nresult = pysx(t"""\n    div(css={rules!s}): "sample"\n""")\n'
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
        f'result = pysx(t"""\n    p: {{{expression}}}\n""")\n'
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
        'result = pysx(t"""\n'
        "    p: {a * 2} {a < 3} {length(rows)} {contains(rows, a)}\n"
        "    p: {all_of(a > 0, a < 3)} {eq(a, 1)} {(lambda: a())}\n"
        '""")\n'
    )
    assert results == []


def test_template_ergonomics_projection_alias_snapshot() -> None:
    results, _ = check(
        'state = signal({"a": [1]})\nitem = state["a"][0]\n'
        'result = pysx(t"""\n    p: {item()}\n""")\n'
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
        "def app():\n"
        '    return pysx(t"""\n'
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
        "def app():\n"
        "    count = signal(0)\n"
        '    return pysx(t"""\n'
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
        "def app():\n"
        '    return pysx(t"""\n'
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
        "def app():\n"
        '    return pysx(f"""\n'
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
        "def app():\n"
        '    return pysx(t"""\n'
        "        Page:\n"
        "            else:\n"
        '                Page: "hi"\n'
        '    """)\n'
    )
    diags, _ = check(body)
    assert any("without a matching" in d["message"] for d in diags), diags


def test_component_names_escape_ruff_function_casing() -> None:
    from pysx.tooling.lint import lint_source

    source = (
        "from pysx import Fragment, pysx\n\n\n"
        "def TreeControls(*children: object, title: str) -> Fragment:\n"
        '    return pysx(t"""section: {title}""")\n\n\n'
        "def BadHelper() -> int:\n"
        "    return 1\n"
    )
    codes = {
        (finding.code, finding.message.split("`")[1])
        for finding in lint_source(source, str(Path.cwd() / "pysx" / "_casing.py"))
        if finding.code == "N802"
    }
    assert codes == {("N802", "BadHelper")}
