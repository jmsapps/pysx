"""Shared compiler contract: lexical loads, original maps and strict props."""

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from pysx.compiler import analyze
from pysx.render import Fragment

if TYPE_CHECKING:
    from collections.abc import Callable


def test_runtime_local_component_is_a_real_closure() -> None:
    source = """from pysx import pysx, styled
def app():
    Local = styled.section(t"color: red")
    return lambda: pysx(t"Local: 'Hello'")
"""
    compilation = analyze(source)
    assert compilation.complete
    namespace: dict[str, object] = {}
    exec(compilation.code(), namespace)
    app = namespace["app"]
    assert callable(app)
    row = app()
    assert callable(row)
    assert "Local" in row.__code__.co_freevars
    result = row()
    assert isinstance(result, Fragment)
    assert result.namespace is not None
    assert "Local" in result.namespace


def test_inline_sibling_lowering_lexical_shadowing_and_exact_projection() -> None:
    source = r'''from pysx import pysx, styled
from pysx.native import Strong as lower
def app():
    Local = styled.section(t"color: red")
    def row():
        return pysx(t"p: '😀 {{brace}} \t'; lower; Local: 'closed'")
    return row
def shadow(lower):
    return pysx(t"br; lower; div")
'''
    compilation = analyze(source)
    assert compilation.complete
    references = sorted(compilation.references, key=lambda reference: reference.span.start)
    assert [reference.name for reference in references] == ["lower", "Local", "lower"]
    projection = compilation.projection()

    for reference in references:
        assert source[reference.span.start : reference.span.end] == reference.name
        generated = projection.text.index(f"'{reference.name}': {reference.name}")
        generated += len(f"'{reference.name}': ")
        assert projection.span(generated, generated + len(reference.name)) == next(
            ref.span for ref in references if ref.name == reference.name
        )
    namespace: dict[str, object] = {}
    exec(compilation.code(), namespace)
    app = namespace["app"]
    assert callable(app)
    row = app()
    assert callable(row)
    assert "Local" in row.__code__.co_freevars
    from pysx import render

    result = render(cast("Callable[[], Fragment]", row))
    assert "<strong></strong>" in result.body
    assert "closed</section>" in result.body


@pytest.mark.parametrize("raw", [False, True])
def test_inline_sibling_raw_assembled_source_map(raw: bool) -> None:
    prefix = "rt" if raw else "t"
    source = (
        "from pysx import pysx\nfrom pysx.native import Strong as Lower\n"
        f"head = {prefix}\"p: '😀 {{{{brace}}}} \\t'; \"\n"
        'tail = t"Lower(title={\'valid\'}); br"\n'
        "view = pysx(head + tail)\n"
    )
    compilation = analyze(source)
    assert compilation.complete
    assert len(compilation.references) == 1
    tag = compilation.references[0]
    assert source[tag.span.start : tag.span.end] == "Lower"
    projection = compilation.projection()
    prop = projection.text.rindex("title=")
    span = projection.span(prop, prop + 5)
    assert source[span.start : span.end] == "title"


@pytest.mark.parametrize(
    ("imports", "call", "expected"),
    [
        ("from pysx import pysx", "pysx", True),
        ("from pysx import pysx as template", "template", True),
        ("from pysx.render import pysx", "pysx", True),
        ("import pysx as px", "px.pysx", True),
        ("import pysx.render as renderer", "renderer.pysx", True),
        ("from pysx import pysx\ntemplate = pysx", "template", True),
        ("from unrelated import pysx", "pysx", False),
        ("def pysx(value): return value", "pysx", False),
        ("from pysx import pysx\npysx = lambda value: value", "pysx", False),
    ],
)
def test_marker_identity(imports: str, call: str, expected: bool) -> None:
    compilation = analyze(f"{imports}\nview = {call}(t\"Panel: 'hello'\")\n")
    assert bool(compilation.calls) is expected


@pytest.mark.parametrize(
    "body",
    [
        """def app(pysx):
    return pysx(t"Panel: 'hello'")""",
        """def app():
    pysx = lambda value: value
    return pysx(t"Panel: 'hello'")""",
        """views = [pysx(t"Panel: 'hello'") for pysx in consumers]""",
        """views = list(map(lambda pysx: pysx(t"Panel: 'hello'"), consumers))""",
    ],
)
def test_lexical_marker_shadowing(body: str) -> None:
    compilation = analyze("from pysx import pysx\n" + body)
    assert not compilation.calls


def test_nested_templates_preserve_conversions_and_eager_order() -> None:
    source = """from pysx import pysx
events = []
def value(name):
    events.append(name)
    return name
Panel = object()
view = pysx(t"Panel: {value('first')!r} {pysx(t'p: {value(\"nested\")}')} {value('last'):>5}")
"""
    compilation = analyze(source)
    assert compilation.complete
    assert len(compilation.calls) == 2
    namespace: dict[str, object] = {}
    exec(compilation.code(), namespace)
    assert namespace["events"] == ["first", "nested", "last"]
    view = namespace["view"]
    assert isinstance(view, Fragment)
    assert view.template.interpolations[0].conversion == "r"
    assert view.template.interpolations[2].format_spec == ">5"
    projection = compilation.projection()
    ast.parse(projection.text)
    assert projection.text.count(".bind(") == 2


def test_supported_assembly_and_origin_boundary() -> None:
    compilation = analyze("""from pysx import pysx
template = t"Panel: 'hi'"
view = pysx(template + t"\n  p: 'child'")
""")
    # Multiline content on the quote line is intentionally still rejected.
    assert not compilation.complete
    compilation = analyze("""from pysx import pysx
template = t"Panel: 'hi'"
view = pysx(template)
""")
    assert compilation.complete
    assert [reference.name for reference in compilation.references] == ["Panel"]
    foreign = analyze("""from pysx import pysx
template = t"Panel: 'hi'"
def app():
    return pysx(template)
""")
    assert not foreign.complete
    assert "origin unavailable" in foreign.diagnostics[0].message


def test_maps_tag_props_and_non_bmp_original_source() -> None:
    source = """from pysx import pysx
emoji = '😀'; view = pysx(t"Panel(title={emoji}): 'Hello'")
"""
    compilation = analyze(source)
    projection = compilation.projection()
    tag = compilation.references[0]
    assert source[tag.span.start : tag.span.end] == "Panel"
    generated = projection.text.index("'Panel': Panel") + len("'Panel': ")
    assert projection.span(generated, generated + 5) == tag.span
    prop = projection.text.rindex("title=")
    span = projection.span(prop, prop + 5)
    assert source[span.start : span.end] == "title"


def test_cleanup_uncertainty_and_hygienic_helper() -> None:
    source = """from pysx import pysx
def app(_pysx_compiler):
    return pysx(t"Panel: 'hello'")
"""
    compilation = analyze(source)
    assert compilation.helper != "_pysx_compiler"
    assert not analyze('from pysx import pysx\nview = pysx(t"Panel(")').complete
    assert not analyze('from pysx import pysx\nview = pysx(t"').complete
    type_only = analyze("""from typing import TYPE_CHECKING
from pysx import pysx
if TYPE_CHECKING:
    from components import Panel
view = pysx(t"Panel: 'hi'")
""")
    assert not type_only.complete
    assert type_only.diagnostics[0].code == "type-only-component"


@pytest.mark.parametrize(
    "body",
    [
        'try:\n        raise ValueError("oops")\n'
        '    except ValueError as _pysx_compiler:\n        return pysx(t"p: \'hello\'")',
        "match 1:\n        case _pysx_compiler:\n            return pysx(t\"p: 'hello'\")",
        "match [1]:\n        case [*_pysx_compiler]:\n            return pysx(t\"p: 'hello'\")",
        "match {}:\n        case {**_pysx_compiler}:\n            return pysx(t\"p: 'hello'\")",
    ],
)
def test_helper_hygiene_for_exception_and_pattern_bindings(body: str) -> None:
    from pysx.render import render

    source = f"from pysx import pysx\ndef app():\n    {body}\n"
    compilation = analyze(source)
    assert compilation.helper != "_pysx_compiler"
    namespace: dict[str, object] = {}
    exec(compilation.code(), namespace)
    app = namespace["app"]
    assert callable(app)
    assert "hello" in render(lambda: cast("Fragment", app())).body


def test_class_body_capture_preserves_locals_closures_and_missing_branches() -> None:
    from pysx.render import render

    source = '''from pysx import pysx, styled
def build():
    Outer = styled.strong(t"color: red")
    class Widget:
        Local = styled.em(t"color: blue")
        locals = None
        fragment = pysx(t"""
if {False}:
    Missing: "bad"
else:
    Local: "local"
    Outer: "outer"
""")
    return Widget.fragment
'''
    namespace: dict[str, object] = {}
    exec(analyze(source).code(), namespace)
    captured = namespace["build"]
    assert callable(captured)
    body = render(lambda: cast("Fragment", captured())).body
    assert "<em" in body
    assert "local" in body
    assert "<strong" in body
    assert "outer" in body
    assert "bad" not in body

    namespace = {}
    exec(analyze(source.replace("if {False}", "if {True}")).code(), namespace)
    build = namespace["build"]
    assert callable(build)

    with pytest.raises(NameError, match="Missing"):
        render(lambda: cast("Fragment", build()))


@pytest.mark.parametrize(
    "prefix",
    [
        '"Module docstring"\nfrom __future__ import annotations\n',
        '"Module docstring"; from __future__ import annotations; ',
        "#!/usr/bin/env python\n# coding: utf-8\n",
    ],
)
def test_preserves_module_docstring_and_future_imports(prefix: str) -> None:
    source = prefix + "from pysx import pysx\nPanel = object()\nview = pysx(t\"Panel: 'hi'\")\n"
    compilation = analyze(source)
    assert compilation.complete
    generated = compilation.projection()
    compile(generated.text, "projection.py", "exec")
    namespace: dict[str, object] = {}
    exec(compilation.code(), namespace)

    if prefix.startswith('"'):
        assert namespace["__doc__"] == "Module docstring"


def test_generated_validation_supports_await_and_assignment_expressions() -> None:
    source = """from pysx import pysx
async def app():
    return pysx(t"Panel(title={await title()}, count={(count := 3)}): 'hello'")
"""
    compilation = analyze(source)
    assert compilation.complete
    compile(compilation.projection().text, "projection.py", "exec")


def test_actual_ruff_preserves_only_template_used_imports(tmp_path: Path) -> None:
    source = """from pysx import pysx
from components import Panel, Unused
view = pysx(t"Panel: 'hello'")
"""
    path = tmp_path / "projection.py"
    path.write_text(analyze(source).projection().text)
    checked = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "F401,F821", str(path)],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert checked.returncode == 1
    assert "`components.Unused` imported but unused" in checked.stdout
    assert "`components.Panel` imported but unused" not in checked.stdout
    assert "`pysx.pysx` imported but unused" not in checked.stdout


def test_lowercase_components_and_reserved_native_tags() -> None:
    compilation = analyze('''from pysx import pysx
from components import row, third
view = pysx(t"""
    row:
      section: 'native'
      Other:
        if {True}:
          third: 'child'
""")
''')
    assert compilation.complete
    assert [reference.name for reference in compilation.references] == ["row", "Other", "third"]


def test_rebound_assembly_is_not_guessed() -> None:
    compilation = analyze("""from pysx import pysx
template = t"One: 'hi'"
template = t"Two: 'hi'"
view = pysx(template)
""")
    assert not compilation.complete
    assert not compilation.calls


def test_ambiguous_marker_withholds_import_cleanup() -> None:
    compilation = analyze("""from pysx import pysx
view = pysx(t"Panel: 'hi'")
pysx = other_consumer
""")
    assert not compilation.complete
    assert compilation.diagnostics[0].code == "ambiguous-marker"


def test_partial_unpacking_is_conservatively_unknown() -> None:
    compilation = analyze("""from functools import partial
from pysx import pysx
Panel = partial(base, **props)
view = pysx(t"Panel: 'hello'")
""")
    assert not compilation.complete
    assert compilation.diagnostics[0].code == "partial-schema-unknown"
    assert compilation.references[0].name == "Panel"


def test_incomplete_python_error_maps_to_its_source_line() -> None:
    source = """from pysx import pysx
emoji = '😀'; view = pysx(t"Panel: 'hello'
"""
    compilation = analyze(source)
    assert not compilation.complete
    assert compilation.diagnostics[0].span.start >= source.index("emoji")


def test_legacy_inventory_names_keep_their_compatibility_semantics() -> None:
    source = """from pysx import pysx, styled
Card = styled.section(t"color: red")
view = pysx(t"Panel: 'hi'", namespace={"Panel": Card})
"""
    compilation = analyze(source)
    assert compilation.complete
    assert not compilation.calls
    namespace: dict[str, object] = {}
    exec(compilation.code(), namespace)
    view = namespace["view"]
    assert isinstance(view, Fragment)
    assert view.namespace is not None
    assert "Panel" in view.namespace


def test_disjoint_attribute_assembly_has_an_explicit_source_diagnostic() -> None:
    source = """from pysx import pysx
view = pysx(t'Panel(title="He' + t'llo"):')
"""
    compilation = analyze(source)
    assert not compilation.complete
    assert "disjoint origins" in compilation.diagnostics[0].message


def test_foreign_and_custom_markup_remains_native() -> None:
    compilation = analyze('''from pysx import pysx
view = pysx(t"""
    svg:
      circle(cx="10", cy="20")
      foreignObject:
        custom-widget: 'hello'
""")
''')
    assert compilation.complete
    assert not compilation.references
    namespace: dict[str, object] = {}
    exec(compilation.code(), namespace)
    assert isinstance(namespace["view"], Fragment)
    compile(compilation.projection().text, "projection.py", "exec")


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
@pytest.mark.parametrize("invalid", [False, True])
def test_workspace_signatures_without_import_execution(
    tmp_path: Path, checker: str, invalid: bool
) -> None:
    components = tmp_path / "components.py"
    components.write_text("""from functools import partial
from pysx import Fragment, pysx, styled
def panel(*children: object, title: str = "Default") -> Fragment:
    return pysx(t"section: {title} {children}")
Panel = partial(panel, title="Fixed")
Card = styled(styled.section(t"color: red"))(t"padding: 2px")
raise AssertionError("application must not execute during analysis")
""")
    expression = "42" if invalid else "'Hello'"
    source = f"""from pysx import pysx, div as Box
from components import Panel, Card
view = pysx(t"Panel(title={{{expression}}}): 'Child'")
native = pysx(t'''
    Card(className={{{expression}}}, data-kind='card', aria-label='Title', variant={{None}}): 'Card'
''')
alias = pysx(t"{{Box}}(id={{'hello'}}): 'Child'")
"""
    compilation = analyze(source, str(tmp_path / "app.py"))
    assert compilation.complete
    path = tmp_path / "projection.py"
    path.write_text(compilation.projection().text)
    config = tmp_path / "pyrightconfig.json"
    config.write_text(
        json.dumps(
            {
                "typeCheckingMode": "strict",
                "pythonVersion": "3.14",
                "extraPaths": [str(tmp_path), str(Path(__file__).resolve().parents[1])],
                "include": ["projection.py"],
                "reportUnusedImport": False,
            }
        )
    )
    args = [sys.executable, "-m", checker]

    if checker == "mypy":
        args.extend(["--strict", "--follow-imports=silent"])
    else:
        args.extend(["--project", str(config)])
    result = subprocess.run(
        [*args, str(path)], capture_output=True, text=True, timeout=60, check=False
    )
    output = result.stdout + result.stderr
    assert result.returncode == (1 if invalid else 0), output

    if invalid:
        assert "int" in output or "42" in output, output
        assert "str" in output, output


def test_relative_imports_and_marker_reexports(tmp_path: Path) -> None:
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "markers.py").write_text("from pysx import pysx\n")
    source = """from .markers import pysx as template
from .components import Panel
view = template(t"Panel: 'hi'")
"""
    compilation = analyze(source, str(package / "view.py"))
    assert compilation.complete
    assert len(compilation.calls) == 1
    assert (
        compilation.calls[0].scope.identity(ast.Name("Panel", ast.Load())) == "app.components.Panel"
    )


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
@pytest.mark.parametrize("invalid", [False, True])
def test_factory_native_children_projection(tmp_path: Path, checker: str, invalid: bool) -> None:
    (tmp_path / "surfaces.py").write_text(
        "from typing import TYPE_CHECKING\nfrom pysx import styled\n"
        "if TYPE_CHECKING:\n    from pysx.styled_native import StyledDiv\n"
        'def page() -> StyledDiv:\n    return styled.div(t"color: red")\n'
        'Page = page()\nShell = styled(Page)(t"padding: 2px")\n'
    )
    value = "42" if invalid else "'panel'"
    compilation = analyze(
        "from pysx import pysx\nfrom surfaces import Shell\n"
        f"view = pysx(t\"br; Shell(id={{{value}}}): 'child'; br\")\n",
        str(tmp_path / "view.py"),
        workspace_roots=(tmp_path,),
    )
    projection = compilation.projection().text
    assert "_native.Div" in projection
    assert "children=" not in projection
    target = tmp_path / "view.py"
    target.write_text(projection)
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "pyrightconfig.json").write_text(
        json.dumps(
            {"typeCheckingMode": "strict", "pythonVersion": "3.14", "extraPaths": [str(root)]}
        )
    )
    args = [sys.executable, "-m", checker]

    if checker == "mypy":
        args.append("--strict")
    else:
        args.extend(["--project", str(tmp_path / "pyrightconfig.json")])
    result = subprocess.run(
        [*args, str(target)], cwd=root, capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == (1 if invalid else 0), result.stdout + result.stderr


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
@pytest.mark.parametrize("invalid", [False, True])
def test_typed_projection_props(tmp_path: Path, checker: str, invalid: bool) -> None:
    expression = "row.id" if invalid else "row.title"
    source = f"""from dataclasses import dataclass
from functools import partial
from pysx import Children, Fragment, each, pysx, signal, styled
@dataclass
class Row:
    id: int
    title: str
def panel(*, title: str, children: Children | None = None) -> Fragment:
    return pysx(t"section: {{title}} {{children}}")
Panel = partial(panel)
Styled = styled(panel)(t"color: red")
rows = signal([Row(1, "one")])
view = each(rows,
    lambda row: pysx(t"br; Panel(title={{{expression}}}): 'Content'; br"),
    key=lambda row: row.id)
native = pysx(t"br; input(bindValue={{signal('value')}}, disabled={{False}}); br")
decorated = pysx(t"br; Styled(title={{'hello'}}, variant={{None}}): 'child'; br")
"""
    compilation = analyze(source)
    assert compilation.complete
    path = tmp_path / "projection.py"
    path.write_text(compilation.projection().text)
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "pyrightconfig.json").write_text(
        '{"typeCheckingMode":"strict","pythonVersion":"3.14","extraPaths":['
        + repr(str(root)).replace("'", '"')
        + '],"reportUnusedImport":false}'
    )
    command = [sys.executable, "-m", checker]

    if checker == "mypy":
        command.extend(["--strict", "--no-incremental"])
    result = subprocess.run(
        [*command, str(path)], cwd=root, text=True, capture_output=True, timeout=60, check=False
    )
    output = result.stdout + result.stderr

    if invalid:
        assert result.returncode == 1, output
        assert '"int"' in output, output
        assert '"str"' in output, output
    else:
        assert result.returncode == 0, output


def test_unsaved_imported_schema_and_explicit_workspace_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    source = """from pysx import pysx
from components import Card
view = pysx(t"Card(disabled={True}): 'Go'")
"""
    buffers = {
        str(
            root / "components.py"
        ): 'from pysx import styled\nCard = styled.button(t"color: red")\n'
    }
    compilation = analyze(
        source, str(tmp_path / "app.py"), workspace_roots=(root,), buffers=buffers
    )
    assert compilation.complete
    assert "_native.Button" in compilation.projection().text


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
@pytest.mark.parametrize("kind", ["default", "required", "children"])
def test_required_default_and_children_props(tmp_path: Path, checker: str, kind: str) -> None:
    parameter = "title: str" if kind == "required" else 'title: str = "Default"'
    markup = "panel: 'Child'" if kind == "children" else "panel:"
    source = f'''from pysx import Fragment, pysx
def panel(*, {parameter}) -> Fragment:
    return pysx(t"p: {{title}}")
view = pysx(t"{markup}")
'''
    fixture = tmp_path / "required.py"
    fixture.write_text(analyze(source).projection().text)
    args = [sys.executable, "-m", checker]

    if checker == "mypy":
        args.append("--strict")
    result = subprocess.run(
        [*args, str(fixture)], text=True, capture_output=True, timeout=60, check=False
    )
    output = result.stdout + result.stderr
    assert result.returncode == (0 if kind == "default" else 1), output

    if kind != "default":
        assert ("title" if kind == "required" else "children") in output, output


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
@pytest.mark.parametrize("valid", [False, True])
def test_callable_instances_and_component_return_contract(
    tmp_path: Path, checker: str, valid: bool
) -> None:
    return_type = "Fragment" if valid else "int"
    result = 'pysx(t"p: {title} {children}")' if valid else "3"
    source = f"""from pysx import Fragment, pysx
class Widget:
    def __call__(self, *children: object, title: str) -> {return_type}:
        return {result}
Panel = Widget()
view = pysx(t"Panel(title={{'hello'}}): 'Child'")
"""
    fixture = tmp_path / "callable_instance.py"
    fixture.write_text(analyze(source).projection().text)
    args = [sys.executable, "-m", checker]

    if checker == "mypy":
        args.append("--strict")
    result_checked = subprocess.run(
        [*args, str(fixture)], capture_output=True, text=True, timeout=60, check=False
    )
    output = result_checked.stdout + result_checked.stderr
    assert result_checked.returncode == (0 if valid else 1), output

    if not valid:
        assert "component_result" in output, output
