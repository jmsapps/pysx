"""Conservative workspace bindings share native schemas and typed projections."""

import ast
from typing import TYPE_CHECKING

import pytest

from pysx.check import diagnostics_for_source
from pysx.compiler import analyze, native_tag_for
from pysx.compiler_scope import ScopeIndex
from pysx.quality import type_project

if TYPE_CHECKING:
    from pathlib import Path


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
        "from pysx import pysx\nfrom pysx.native import Div as Base\n"
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
        "from pysx import pysx\nfrom pysx.native import Div as Base\n"
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


def test_resolve_native_unsaved_imported_styled_ancestry_without_execution(tmp_path: Path) -> None:
    producer = tmp_path / "producer.py"
    producer.write_text("raise AssertionError('saved producer must not execute')\n")
    source = (
        "from pysx import pysx\nfrom producer import Panel as Card\n"
        "view = pysx(t\"Card(disabled={True}, data-kind='action', aria-label='Go')\")\n"
    )
    buffers = {
        str(producer): "from pysx.styled import styled as decorate\n"
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
        "from pysx.styled import styled as decorate\n"
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
