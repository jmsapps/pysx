"""Actual strict tools and current buffers preserve ordinary template imports."""

import subprocess
import sys
from typing import TYPE_CHECKING, cast

import pytest

from pysx.compiler.compiler import analyze
from pysx.tooling.analysis import projection_for_source, utf16_offsets
from pysx.tooling.check import diagnostics_for_source
from pysx.tooling.lint import lint_source
from pysx.tooling.quality import type_project

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pysx import Fragment

SOURCE = """from pysx import pysx
from pysx.components.native import Strong, Em
view = pysx(t"br; Strong: 'Hello'")
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
    return pysx(t"br; Panel(title={'valid'}); br")
"""
    path.write_text(source, encoding="utf-8")
    assert type_project(tmp_path, backend) == 0
    path.write_text(source.replace("{'valid'}", "{42}"), encoding="utf-8")
    assert type_project(tmp_path, backend) == 1


def test_template_import_usage_nonempty_coverage(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no Python sources"):
        type_project(tmp_path, "pyright")


@pytest.mark.parametrize("backend", ["mypy", "pyright"])
def test_template_import_usage_actual_cli_cleanup_and_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    from pysx import render

    (tmp_path / "pyproject.toml").write_text(
        '[tool.mypy]\nstrict=true\npython_version="3.14"\n'
        '[tool.pyright]\ntypeCheckingMode="strict"\npythonVersion="3.14"\n'
    )
    marker = tmp_path / "executed"
    (tmp_path / "usage_components.py").write_text(
        "from pathlib import Path\n\nfrom pysx import Fragment, pysx, styled\n\n\n"
        "def card(*, title: str) -> Fragment:\n    return pysx(t'p: {title}')\n"
        "Card = card\nBase = styled.div(t'color: red')\n"
        "Decorated = styled(Base)(t'padding: 2px')\n"
        f"Path({str(marker)!r}).write_text('runtime', encoding='utf-8')\n"
    )
    source = """from usage_components import Card, Decorated

from pysx import Fragment, pysx
from pysx.components.native import Em, Strong as Bold


def app() -> Fragment:
    return pysx(t"Bold: 'Bold'; Decorated: 'Styled'; Card(title='Callable')")
""".replace("\n", "\r\n")
    path = tmp_path / "view.py"
    path.write_bytes(source.encode())
    command = [sys.executable, "-m", "pysx.tooling.quality", "ruff", str(tmp_path)]
    before = subprocess.run(command, capture_output=True, text=True, check=False)
    assert before.returncode == 1, before.stdout + before.stderr
    assert "F401" in before.stdout
    assert "Em" in before.stdout
    cleaned = subprocess.run(
        [*command, "--fix-imports"], capture_output=True, text=True, check=False
    )
    assert cleaned.returncode == 0, cleaned.stdout + cleaned.stderr
    repaired = path.read_bytes().decode()
    assert "Em" not in repaired
    assert "Strong as Bold" in repaired
    assert "Card, Decorated" in repaired
    assert repaired.count("\r\n") == source.count("\r\n")
    assert diagnostics_for_source(repaired, str(path)) == []
    assert type_project(tmp_path, backend) == 0
    assert not marker.exists()
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    namespace: dict[str, object] = {}

    try:
        exec(analyze(repaired, str(path)).code(), namespace)
        app = namespace["app"]
        assert callable(app)
        rendered = render(cast("Callable[[], Fragment]", app))

        try:
            for text in ("Bold", "Styled", "Callable"):
                assert text in rendered.body
        finally:
            rendered.dispose()
        assert marker.read_text() == "runtime"
    finally:
        sys.modules.pop("usage_components", None)


def test_template_import_usage_unresolved_names_withhold_cli_edits(tmp_path: Path) -> None:
    source = SOURCE.replace("Strong: 'Hello'", "Ghost: 'missing'")
    findings = lint_source(source, str(tmp_path / "view.py"))
    assert any(item.code == "F821" for item in findings)
    assert not any(item.edits for item in findings)
    path = tmp_path / "view.py"
    path.write_bytes(source.encode())
    result = subprocess.run(
        [sys.executable, "-m", "pysx.tooling.quality", "ruff", str(tmp_path), "--fix-imports"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert path.read_bytes() == source.encode()


def test_template_import_usage_prose_and_shadowing_retain_genuine_unused_checks(
    tmp_path: Path,
) -> None:
    prose = SOURCE.replace("Strong: 'Hello'", "p: 'Strong'") + '# Strong\nprose = "Strong"\n'
    assert (
        len(
            [item for item in lint_source(prose, str(tmp_path / "prose.py")) if item.code == "F401"]
        )
        == 2
    )
    shadowed = """from collections.abc import Callable
from pysx import Fragment, pysx
from pysx.components.native import Strong as Tag
def app(Tag: Callable[[], Fragment]) -> Fragment:
    return pysx(t"Tag")
"""
    findings = lint_source(shadowed, str(tmp_path / "shadowed.py"))
    assert len([item for item in findings if item.code == "F401"]) == 1


@pytest.mark.parametrize("backend", ["mypy", "pyright"])
@pytest.mark.parametrize(
    ("tag", "prop", "good", "bad"),
    [
        ("input", "disabled", "False", "'wrong'"),
        ("Empty", "id", "'valid'", "42"),
        ("panel", "title", "'valid'", "42"),
        ("Styled", "title", "'valid'", "42"),
    ],
)
def test_inline_sibling_each_surface_strict_props(
    tmp_path: Path, backend: str, tag: str, prop: str, good: str, bad: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mypy]\nstrict=true\npython_version="3.14"\n'
        '[tool.pyright]\ntypeCheckingMode="strict"\npythonVersion="3.14"\n'
    )
    path = tmp_path / "view.py"
    source = """from pysx import Fragment, pysx, styled
def panel(*, title: str) -> Fragment:
    return pysx(t"p: {title}")
Styled = styled(panel)(t"color: red")
Empty = styled.div(t"")
"""

    for value, expected in [(good, 0), (bad, 1)]:
        path.write_text(source + f'view = pysx(t"br; {tag}({prop}={{{value}}}); br")\n')
        assert type_project(tmp_path, backend) == expected
