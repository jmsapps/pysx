"""Actual strict tools and current buffers preserve ordinary template imports."""

from typing import TYPE_CHECKING

import pytest

from pysx.analysis import projection_for_source, utf16_offsets
from pysx.check import diagnostics_for_source
from pysx.lint import lint_source
from pysx.quality import type_project

if TYPE_CHECKING:
    from pathlib import Path

SOURCE = """from pysx import pysx
from pysx.native import Strong, Em
view = pysx(t"Strong: 'Hello'")
"""


def test_template_import_usage_real_ruff_and_safe_cleanup(tmp_path: Path) -> None:
    path = tmp_path / "view.py"
    findings = lint_source(SOURCE, str(path))
    unused = [item for item in findings if item.code == "F401"]
    assert len(unused) == 1
    assert "Em" in unused[0].message
    assert unused[0].edits
    source = SOURCE

    for edit in sorted(unused[0].edits, key=lambda item: item.span.start, reverse=True):
        source = source[: edit.span.start] + edit.text + source[edit.span.end :]
    assert "Strong" in source
    assert not any(item.code == "F401" for item in lint_source(source, str(path)))


@pytest.mark.parametrize("replacement", ["p: 'unused'", "Strong(title=", "Ghost: 'missing'"])
def test_template_import_usage_removed_or_incomplete(tmp_path: Path, replacement: str) -> None:
    source = SOURCE.replace("Strong: 'Hello'", replacement)
    findings = lint_source(source, str(tmp_path / "view.py"))
    assert any(item.code == "F401" for item in findings)

    if replacement.endswith("="):
        assert not any(item.edits for item in findings)

    if "Ghost" in replacement:
        assert any(
            item.code == "F821" and source[item.span.start : item.span.end] == "Ghost"
            for item in findings
        )


def test_template_import_usage_unicode_and_lexical_scope(tmp_path: Path) -> None:
    source = """from pysx import pysx, Fragment
def producer():
    def Panel() -> Fragment: return pysx(t"p: 'one'")
    return pysx(t"Panel: '😀'")
def consumer():
    return pysx(t"Panel: 'missing'")
"""
    path = str(tmp_path / "view.py")
    diagnostics = diagnostics_for_source(source, path)
    unknown = [item for item in diagnostics if "unknown component" in item["message"]]
    assert len(unknown) == 1
    assert unknown[0]["line"] == 5
    model = projection_for_source(source, path)
    assert len(model["map"]) == utf16_offsets(model["text"])[-1]
    assert (
        len(
            [
                site
                for site in model["sites"]
                if site["role"] == "component" and not site.get("native")
            ]
        )
        == 2
    )


@pytest.mark.parametrize("backend", ["mypy", "pyright"])
def test_template_import_usage_real_strict_tools(tmp_path: Path, backend: str) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """[tool.mypy]
strict = true
python_version = "3.14"
[tool.pyright]
typeCheckingMode = "strict"
pythonVersion = "3.14"
""",
        encoding="utf-8",
    )
    path = tmp_path / "view.py"
    source = """from pysx import Fragment, pysx
def Panel(*, title: str) -> Fragment:
    return pysx(t"p: {title}")
def app() -> Fragment:
    return pysx(t"Panel(title={'valid'})")
"""
    path.write_text(source, encoding="utf-8")
    assert type_project(tmp_path, backend) == 0
    path.write_text(source.replace("{'valid'}", "{42}"), encoding="utf-8")
    assert type_project(tmp_path, backend) == 1


def test_template_import_usage_nonempty_coverage(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no Python sources"):
        type_project(tmp_path, "pyright")
