"""Workspace projections keep current source, module identity and edit provenance."""

import ast
import json
from typing import TYPE_CHECKING, cast

import pytest

from pysx.analysis import projection_for_source, utf16_offsets
from pysx.editor_model import workspace_model
from pysx.editor_types import diagnostics


def test_callable_prop_spelling_does_not_make_it_a_native_site() -> None:
    source = """from pysx import Fragment, html
def Panel(*, title: str, maxWidth: int = 0) -> Fragment:
    return html(t'p: {title}')
view = html(t'Panel(title={"x"})')
native = html(t'input(title="x")')
"""
    model = projection_for_source(source, "/private/tmp/prop_sites.py")
    props = [site for site in model["sites"] if site["role"] == "prop"]
    assert len(props) == 2
    assert props[0].get("native") is not True
    assert props[1].get("native") is True


def test_project_handoff_retains_genuine_unused_function_diagnostics(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyright]\ntypeCheckingMode = "strict"\nreportUnusedFunction = false\n'
    )
    (tmp_path / "view.py").write_text("""from pysx import Fragment, html
def app() -> Fragment:
    def branch() -> Fragment:
        return html(t'p: "used"')
    def dead() -> Fragment:
        return html(t'p: "unused"')
    return html(t'branch:')
""")
    revision = "_pysx_revision_functions"
    models = workspace_model({"root": str(tmp_path), "revision": revision, "buffers": {}})
    directory = tmp_path / revision
    directory.mkdir()
    (directory / "__init__.py").write_text("")

    for model in models:
        path = directory / model["relative"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(model["text"])
    result = diagnostics(directory, tmp_path)
    reported = cast("list[dict[str, object]]", result["generalDiagnostics"])
    unused = [item for item in reported if item.get("rule") == "reportUnusedFunction"]
    assert len(unused) == 1
    assert "dead" in str(unused[0]["message"])


if TYPE_CHECKING:
    from pathlib import Path

    from pysx.editor_model import Request


def test_model_cache_rebases_utf16_and_refreshes_versions(tmp_path: Path) -> None:
    producer = tmp_path / "components.py"
    producer.write_text(
        "from pysx import Fragment, html\ndef Panel(*, title: str) -> Fragment: "
        'return html(t"p: {title}")\n'
    )
    consumer = tmp_path / "view.py"
    source = (
        "from components import Panel\nfrom pysx import html\n"
        "view = html(t\"Panel(title={'😀'})\")\n"
    )
    consumer.write_text(source)
    previous = workspace_model(
        {"root": str(tmp_path), "revision": "_pysx_revision_short", "buffers": {}}
    )
    # Exercise the real JSON worker boundary rather than only Python tuple maps.
    previous = json.loads(json.dumps(previous))
    current = workspace_model(
        {
            "root": str(tmp_path),
            "revision": "_pysx_revision_much_longer",
            "buffers": {str(consumer): {"source": source, "version": 12}},
            "previous": previous,
        }
    )
    model = next(value for value in current if value["filename"] == str(consumer))
    fresh = workspace_model(
        {
            "root": str(tmp_path),
            "revision": "_pysx_revision_much_longer",
            "buffers": {str(consumer): {"source": source, "version": 12}},
        }
    )
    expected = next(value for value in fresh if value["filename"] == str(consumer))
    assert json.loads(json.dumps(model)) == json.loads(json.dumps(expected))
    assert model["version"] == 12
    assert len(model["map"]) == utf16_offsets(model["text"])[-1]


def test_model_cache_invalidates_imported_signature_and_ruff_config(tmp_path: Path) -> None:
    producer = tmp_path / "components.py"
    producer.write_text(
        "from pysx import Children, Fragment, html\n"
        "def Panel(*, children: Children | None = None) -> Fragment:\n"
        '    return html(t"p: {children}")\n'
    )
    consumer = tmp_path / "view.py"
    consumer.write_text(
        "import math\nfrom components import Panel\nfrom pysx import html\n"
        "view = html(t\"Panel: 'child'\")\n"
    )
    previous = workspace_model(
        {"root": str(tmp_path), "revision": "_pysx_revision_first", "buffers": {}}
    )
    producer.write_text(
        "from pysx import Fragment, html\n"
        'def Panel(*children: object) -> Fragment: return html(t"p: {children}")\n'
    )
    current = workspace_model(
        {
            "root": str(tmp_path),
            "revision": "_pysx_revision_signature",
            "buffers": {},
            "previous": previous,
        }
    )
    model = next(value for value in current if value["filename"] == str(consumer))
    assert "children=" not in model["text"]
    assert any("F401" in value["message"] for value in model["diagnostics"])
    previous = current
    (tmp_path / "ruff.toml").write_text('extend = "base.toml"\n')
    (tmp_path / "base.toml").write_text('lint.ignore = ["F401"]\n')
    current = workspace_model(
        {
            "root": str(tmp_path),
            "revision": "_pysx_revision_second",
            "buffers": {},
            "previous": previous,
        }
    )
    model = next(value for value in current if value["filename"] == str(consumer))
    assert "children=" not in model["text"]
    assert not any("F401" in value["message"] for value in model["diagnostics"])
    (tmp_path / "base.toml").write_text("lint.ignore = []\n")
    current = workspace_model(
        {
            "root": str(tmp_path),
            "revision": "_pysx_revision_third",
            "buffers": {},
            "previous": current,
        }
    )
    model = next(value for value in current if value["filename"] == str(consumer))
    assert any("F401" in value["message"] for value in model["diagnostics"])


def test_editor_model_coherent_unsaved_graph_and_original_edits(tmp_path: Path) -> None:
    producer = tmp_path / "producer.py"
    producer.write_text(
        "from pysx import Fragment, html\n"
        'def Panel(*, title: str) -> Fragment: return html(t"p: {title}")\n',
        encoding="utf-8",
    )
    consumer = tmp_path / "consumer.py"
    consumer.write_text("raise RuntimeError('never execute applications')\n", encoding="utf-8")
    current = (
        "from pysx import html\nfrom producer import Panel\nimport math\n"
        "view = html(t\"Panel(title={'😀 current'})\")\n"
    )
    models = workspace_model(
        {
            "root": str(tmp_path),
            "revision": "_pysx_revision_test",
            "buffers": {str(consumer): {"source": current, "version": 7}},
        }
    )
    model = next(item for item in models if item["filename"] == str(consumer))
    assert model["version"] == 7
    assert model["source"] == current
    assert "from _pysx_revision_test.producer import Panel" in model["text"]
    assert len(model["map"]) == utf16_offsets(model["text"])[-1]
    assert any("math" in item["message"] for item in model["diagnostics"])
    assert not any("producer.Panel" in item["message"] for item in model["diagnostics"])
    assert model["edits"]

    for start, end, replacement in model["edits"]:
        assert current[start:end] == "import math\n"
        assert replacement == ""


def test_editor_model_dotted_import_identity_and_src_layout(tmp_path: Path) -> None:
    package = tmp_path / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "sub.py").write_text("value: str = 'typed'\n", encoding="utf-8")
    consumer = tmp_path / "consumer.py"
    consumer.write_text("import pkg.sub\nvalue: str = pkg.sub.value\n", encoding="utf-8")
    models = workspace_model(
        {
            "root": str(tmp_path),
            "revision": "_pysx_revision_test",
            "buffers": {},
        }
    )
    model = next(item for item in models if item["relative"] == "consumer.py")
    tree = ast.parse(model["text"])
    assert isinstance(tree.body[0], ast.Import)
    assert tree.body[0].names[0].name == "_pysx_revision_test.pkg.sub"
    assert isinstance(tree.body[1], ast.Assign)
    assert ast.unparse(tree.body[1]) == "pkg = _pysx_revision_test.pkg"
    assert any(item["relative"] == "pkg/sub.py" for item in models)
    assert not model["edits"]


def test_editor_model_incomplete_markup_blocks_import_cleanup(tmp_path: Path) -> None:
    source = 'from pysx import html\nimport math\nview = html(t"p(title=")\n'
    (tmp_path / "view.py").write_text(source, encoding="utf-8")
    model = workspace_model(
        {
            "root": str(tmp_path),
            "revision": "_pysx_revision_test",
            "buffers": {},
        }
    )[0]
    assert not model["complete"]
    assert model["diagnostics"]
    assert not model["edits"]


def test_editor_model_rejects_ambiguous_layout_and_revision_conflicts(tmp_path: Path) -> None:
    (tmp_path / "view.py").write_text("_pysx_revision_test = 1\n", encoding="utf-8")
    request: Request = {"root": str(tmp_path), "revision": "_pysx_revision_test", "buffers": {}}
    with pytest.raises(ValueError, match="conflicts"):
        workspace_model(request)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "view.py").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="ambiguous"):
        workspace_model(request)


def test_editor_owned_types_check_snapshot_and_clean_configuration(tmp_path: Path) -> None:
    producer = tmp_path / "producer.py"
    producer.write_text(
        "raise RuntimeError('never import original applications')\n", encoding="utf-8"
    )
    typed = (
        "from pysx import Fragment, html\n"
        'def Panel(*, title: str) -> Fragment: return html(t"p: {title}")\n'
    )
    (tmp_path / "consumer.py").write_text(
        'from pysx import html\nfrom producer import Panel\nview = html(t"Panel(title={42})")\n',
        encoding="utf-8",
    )
    revision = "_pysx_revision_test"
    models = workspace_model(
        {
            "root": str(tmp_path),
            "revision": revision,
            "buffers": {str(producer): {"source": typed, "version": 2}},
        }
    )
    directory = tmp_path / revision
    directory.mkdir()
    (directory / "__init__.py").write_text("", encoding="utf-8")

    for model in models:
        target = directory / model["relative"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(model["text"], encoding="utf-8")
    result = diagnostics(directory, tmp_path)
    assert "reportArgumentType" in str(result["generalDiagnostics"])
    assert "title" in str(result["generalDiagnostics"])
    assert not (tmp_path / f"{revision}.json").exists()
    assert "raise RuntimeError" in producer.read_text(encoding="utf-8")
