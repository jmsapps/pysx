"""The package root stays canonical: one initializer, no compatibility or alias layer."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, get_type_hints

import pytest

from pysx import StaticAsset
from pysx.tooling.quality import read_source, source_files

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "pysx"
SUBSYSTEMS = (
    "compiler",
    "components",
    "development",
    "hosting",
    "reactive",
    "rendering",
    "runtime",
    "styling",
    "tooling",
)


def canonical_sources() -> Iterator[Path]:
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path


def test_canonical_layout_root_holds_only_the_package_initializer() -> None:
    assert [path.name for path in sorted(PACKAGE.glob("*.py"))] == ["__init__.py"]


def test_canonical_layout_directories_are_subsystems_or_assets() -> None:
    directories = {
        path.name for path in PACKAGE.iterdir() if path.is_dir() and path.name != "__pycache__"
    }
    assert directories == {*SUBSYSTEMS, "static"}


def test_canonical_layout_subsystem_initializers_export_nothing() -> None:
    for name in SUBSYSTEMS:
        tree = ast.parse((PACKAGE / name / "__init__.py").read_text(encoding="utf-8"))
        statements = [node for node in tree.body if not isinstance(node, ast.Expr)]
        assert statements == [], f"pysx/{name}/__init__.py must not re-export or redirect"


def imported_modules(tree: ast.Module, package: str) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = package.rsplit(".", node.level - 1)[0] if node.level else ""
            yield f"{prefix}.{node.module}" if node.level and node.module else node.module or prefix


def test_canonical_imports_carry_no_retired_module_path() -> None:
    offenders: list[str] = []

    for path in canonical_sources():
        package = ".".join(path.relative_to(ROOT).with_suffix("").parts[:-1])
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [
            f"{path.relative_to(ROOT)}: {name}"
            for name in imported_modules(tree, package)
            if name.startswith("pysx.") and name.split(".")[1] not in SUBSYSTEMS
        ]

    assert offenders == []


def test_canonical_imports_cover_emitted_runtime_helpers() -> None:
    from pysx.compiler.compiler import analyze

    emitted = analyze("from pysx import pysx\nview = pysx(t\"div: 'hi'\")\n").projection().text
    assert "import pysx.compiler.runtime as" in emitted
    assert "pysx._compiler_runtime" not in emitted


def test_canonical_imports_resolve_the_public_root_api() -> None:
    import pysx
    from pysx.components.composition import Children
    from pysx.hosting.hosting import Application
    from pysx.reactive.reactive import Signal
    from pysx.rendering.render import render

    assert pysx.render is render
    assert pysx.Application is Application
    assert pysx.Signal is Signal
    assert pysx.Children is Children


def test_public_path_annotations_remain_resolvable() -> None:
    assert get_type_hints(StaticAsset.from_path)["path"] is Path
    assert get_type_hints(read_source)["path"] is Path
    assert get_type_hints(source_files)["root"] is Path


@pytest.mark.parametrize("order", ["root", "subsystem", "reversed"])
def test_canonical_isolation_holds_under_any_import_order(order: str, tmp_path: Path) -> None:
    names = [
        "pysx",
        "pysx.compiler.compiler",
        "pysx.rendering.render",
        "pysx.styling.styled",
    ]

    if order == "subsystem":
        names = names[1:] + names[:1]
    elif order == "reversed":
        names.reverse()
    source = f"""
import importlib, sys
for name in {names!r}:
    importlib.import_module(name)
import pysx
render, styled = pysx.render, pysx.styled
assert callable(render) and callable(styled)
for name in reversed({names!r}):
    importlib.import_module(name)
assert pysx.render is render and pysx.styled is styled
assert "uvicorn" not in sys.modules and "pysx.hosting.server" not in sys.modules
assert not any(name == "websockets" or name.startswith("websockets.") for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", source],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
