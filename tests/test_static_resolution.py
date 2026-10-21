"""Conservative workspace bindings share native schemas and typed projections."""

import ast
import textwrap
from typing import TYPE_CHECKING

import pytest

from pysx.compiler.compiler import analyze, native_tag_for
from pysx.compiler.scope import ScopeIndex
from pysx.tooling.check import diagnostics_for_source
from pysx.tooling.quality import type_project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("prefix", ["", "async "])
def test_scope_dispatch_equivalence_function_outer_expressions(prefix: str) -> None:
    tree = ast.parse(
        f"@decorator()\n{prefix}def f(value=(outer := factory())):\n    inner = factory()\n"
    )
    index = ScopeIndex(tree)
    root = index.nodes[tree]
    assert {"f", "outer"} <= root.bindings.keys()
    assert "inner" not in root.bindings

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            assert index.nodes[node].kind == ("function" if node.lineno == 3 else "module")


@pytest.mark.parametrize(
    "expression",
    [
        "[Panel for Panel in source()]",
        "{Panel for Panel in source()}",
        "{Panel: Panel for Panel in source()}",
        "(Panel for Panel in source())",
    ],
)
def test_scope_dispatch_equivalence_comprehension_nonleak(expression: str) -> None:
    tree = ast.parse(
        f"from pysx.components.native import Div as Base\nPanel = Base\nitems = {expression}\n"
    )
    index = ScopeIndex(tree)
    root = index.nodes[tree]
    assert len(root.bindings["Panel"]) == 1
    assert root.identity(ast.Name(id="Panel")) == "pysx.components.native.Div"

    for node in ast.walk(tree.body[-1]):
        if isinstance(node, ast.Call):
            assert index.nodes[node] is root
        elif isinstance(node, ast.Name) and node.id == "Panel":
            assert index.nodes[node].owner("Panel") is not root


@pytest.mark.parametrize(
    "body",
    [
        "try:\n    Panel = Base\nexcept Exception:\n    pass",
        "try:\n    Panel = Base\nexcept* Exception:\n    pass",
        "for item in source():\n    Panel = Base",
        "async for item in source():\n    Panel = Base",
        "while flag:\n    Panel = Base",
        "if flag:\n    while flag:\n        Panel = Base\n    Inside = Base",
    ],
)
def test_scope_dispatch_equivalence_conditional_and_flag_restoration(body: str) -> None:
    tree = ast.parse(
        "from pysx.components.native import Div as Base\nasync def f():\n"
        + textwrap.indent(body, "    ")
        + "\n    After = Base\n    return Panel, After\n"
    )
    index = ScopeIndex(tree)
    function = tree.body[-1]
    assert isinstance(function, ast.AsyncFunctionDef)
    returned = function.body[-1]
    scope = index.nodes[returned]
    assert scope.owner("Panel") is scope
    assert scope.binding("Panel") is None
    assert scope.identity(ast.Name(id="After")) == "pysx.components.native.Div"

    if "Inside" in body:
        assert scope.binding("Inside") is None


@pytest.mark.parametrize(
    "source",
    [
        "try:\n    pass\nexcept Exception as captured:\n    child = source()",
        "match value:\n    case [captured]:\n        child = source()",
    ],
)
def test_scope_dispatch_equivalence_capture_name_and_children(source: str) -> None:
    tree = ast.parse(source)
    index = ScopeIndex(tree)
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))
    assert index.nodes[call].owner("captured") is index.nodes[tree]
    assert index.nodes[call].owner("child") is index.nodes[tree]


def test_scope_dispatch_equivalence_lambda_boundary() -> None:
    tree = ast.parse("f = lambda: (inner := factory())")
    index = ScopeIndex(tree)
    assert "inner" not in index.nodes[tree].bindings
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))
    assert index.nodes[call].kind == "function"
    assert index.nodes[call].owner("inner") is index.nodes[call]


@pytest.mark.parametrize(
    "binding",
    [
        "Panel: object = Base",
        "Panel, Other = Base, object()",
        "[Other, [Panel]] = [object(), [Base]]",
        "with manager as context:\n        Panel = Base",
    ],
)
def test_resolve_native_lexical_bindings(binding: str) -> None:
    source = (
        "from pysx import pysx\nfrom pysx.components.native import Div as Base\n"
        "def app(manager):\n    " + binding + "\n    return pysx(t\"Panel(id='panel')\")\n"
    )
    compilation = analyze(source)
    assert compilation.complete
    call = compilation.calls[0]
    assert native_tag_for("Panel", call.scope, compilation.resolver) == "div"
    assert "_native.Div" in compilation.projection().text
    assert diagnostics_for_source(source, compilation=compilation) == []


@pytest.mark.parametrize(
    "binding",
    [
        "if flag:\n        Panel = Base",
        "try:\n        Panel = Base\n    except Exception:\n        Panel = other",
        "try:\n        Panel = Base\n    except* Exception:\n        Panel = other",
        "for Panel in items:\n        pass",
        "while flag:\n        Panel = Base",
        "with manager as Panel:\n        pass",
        "Panel, *rest = items",
        "Panel, Other = items",
        "Panel, Other = [Base]",
        "Panel, Other = [*items, Base]",
    ],
)
def test_resolve_native_unknown_bindings_remain_clean(binding: str) -> None:
    source = (
        "from pysx import pysx\nfrom pysx.components.native import Div as Base\n"
        "def app(flag, other, items, manager):\n    "
        + binding
        + "\n    return pysx(t\"Panel(id='panel')\")\n"
    )
    compilation = analyze(source)
    assert compilation.complete
    call = compilation.calls[0]
    assert call.scope.owner("Panel") is not None
    assert native_tag_for("Panel", call.scope, compilation.resolver) is None
    assert diagnostics_for_source(source, compilation=compilation) == []


def test_resolve_native_unsaved_imported_styled_ancestry_without_execution(
    tmp_path: Path,
) -> None:
    producer = tmp_path / "producer.py"
    producer.write_text("raise AssertionError('saved producer must not execute')\n")
    source = (
        "from pysx import pysx\nfrom producer import Panel as Card\n"
        "view = pysx(t\"Card(disabled={True}, data-kind='action', aria-label='Go')\")\n"
    )
    buffers = {
        str(producer): "from pysx.styling.styled import styled as decorate\n"
        "Base = decorate.button(t'color: red')\n"
        "Panel = decorate(Base)(t'padding: 2px')\n"
        "raise AssertionError('unsaved producer must not execute')\n"
    }
    filename = str(tmp_path / "view.py")
    compilation = analyze(source, filename, buffers=buffers)
    assert compilation.complete
    assert native_tag_for("Card", compilation.calls[0].scope, compilation.resolver) == "button"
    assert "_native.Button" in compilation.projection().text
    assert diagnostics_for_source(source, filename, buffers=buffers) == []


@pytest.mark.parametrize("backend", ["mypy", "pyright"])
@pytest.mark.parametrize("kind", ["native", "callable", "required", "children"])
def test_resolve_native_actual_strict_props(tmp_path: Path, backend: str, kind: str) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mypy]\nstrict=true\npython_version="3.14"\n'
        '[tool.pyright]\ntypeCheckingMode="strict"\npythonVersion="3.14"\n'
    )
    (tmp_path / "producer.py").write_text(
        "from functools import partial\n"
        "from pysx import Children, Fragment, pysx\n"
        "from pysx.styling.styled import styled as decorate\n"
        'def panel(*, title: str = "Default", children: Children | None = None) -> Fragment:\n'
        '    return pysx(t"p: {title} {children}")\n'
        "def required(*, title: str) -> Fragment:\n"
        '    return pysx(t"p: {title}")\n'
        "Partial = partial(panel)\n"
        "Callable = decorate(Partial)(t'color: red')\n"
        "Base = decorate.button(t'color: red')\n"
        "Native = decorate(Base)(t'padding: 2px')\n"
        "raise AssertionError('type analysis must never execute producers')\n"
    )
    good, bad = {
        "native": (
            "Native(disabled={True}, data-kind='card', aria-label='Go')",
            "Native(disabled={'invalid'})",
        ),
        "callable": ("Callable: 'Child'", "Callable(title={42}): 'Child'"),
        "required": ("required(title={'value'})", "required"),
        "children": ("required(title={'value'})", "required(title={'value'}): 'Child'"),
    }[kind]
    imported = "Native" if kind == "native" else "Callable" if kind == "callable" else "required"
    path = tmp_path / "view.py"

    for markup, expected in [(good, 0), (bad, 1)]:
        path.write_text(
            f'from pysx import pysx\nfrom producer import {imported}\nview = pysx(t"{markup}")\n'
        )
        assert type_project(tmp_path, backend) == expected


@pytest.mark.parametrize(
    "construct",
    [
        "def f(a: Doc('x')) -> None: ...",
        "def f(a) -> Doc('x'): ...",
        "def f(*a: Doc('x'), **k: Doc('y')) -> None: ...",
        "def f(*, a: Doc('x')) -> None: ...",
        "def f(a: Doc('x'), /) -> None: ...",
        "def f[T: Doc('x')](a: T) -> None: ...",
        "class C(metaclass=Meta()): ...",
        "class C[T: Doc('x')]: ...",
        "x: Doc('y') = 1",
        "f = lambda a=Doc('x'): a",
    ],
)
def test_scope_coverage_indexes_every_call(construct: str) -> None:
    tree = ast.parse(construct)
    index = ScopeIndex(tree, package="")
    unindexed = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and node not in index.nodes
    ]
    assert unindexed == []


@pytest.mark.parametrize(
    "definition",
    [
        "from pysx.components.native import Div as Panel",
        "from pysx.components.elements import div as Panel",
        "from pysx.styling.styled import styled\nPanel = styled.div(t'color: red')",
        "from pysx.styling.styled_native import StyledDiv\n"
        "def factory() -> StyledDiv: ...\nPanel = factory()",
    ],
)
def test_canonical_native_static_schema(definition: str) -> None:
    source = f"from pysx import pysx\n{definition}\nview = pysx(t\"Panel(id='panel')\")\n"
    compilation = analyze(source)
    assert compilation.complete
    assert native_tag_for("Panel", compilation.calls[0].scope, compilation.resolver) == "div"
    assert "_native.Div" in compilation.projection().text
    assert diagnostics_for_source(source, compilation=compilation) == []


def test_scope_coverage_annotated_metadata_analyses_cleanly() -> None:
    source = (
        "from typing import Annotated\n"
        "from pysx import pysx\n"
        "def Doc(text: str) -> str: ...\n"
        "def view(a: Annotated[str, Doc('note')]) -> None: ...\n"
        "app = pysx(t\"div(id='root')\")\n"
    )
    assert analyze(source).complete
    assert diagnostics_for_source(source) == []
