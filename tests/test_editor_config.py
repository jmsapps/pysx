"""Project settings take precedence; configuration edits preserve other settings."""

import json
import tomllib
from typing import TYPE_CHECKING

from pysx.editor_config import MIRRORS, RULES, configuration_edit

if TYPE_CHECKING:
    from pathlib import Path


def test_toml_handoff_preserves_strict_settings_and_unrelated_tables(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    source = """[project]
name = "demo"
[tool.pyright] # retained header
typeCheckingMode = "strict" # keep strict
reportUnusedImport = "warning" # existing override
ignore = [
    "generated/**",
]
[tool.ruff]
line-length = 90
"""
    path.write_text(source)
    edit = configuration_edit(tmp_path)
    assert edit is not None
    assert edit["source"] == source
    assert "# keep strict" in edit["text"]
    assert "# existing override" in edit["text"]
    result = tomllib.loads(edit["text"])
    original = tomllib.loads(source)
    settings = original["tool"]["pyright"]
    settings.update(dict.fromkeys(RULES, False), ignore=["generated/**", MIRRORS])
    assert result == original
    path.write_text(edit["text"])
    assert configuration_edit(tmp_path) is None


def test_jsonc_handoff_preserves_comments_extends_and_other_settings(tmp_path: Path) -> None:
    (tmp_path / "base.json").write_text(
        '{"typeCheckingMode": "strict", "ignore": ["generated/**"]}'
    )
    path = tmp_path / "pyrightconfig.json"
    path.write_text("""{
  // retain inheritance and strict typing
  "extends": "base.json",
  "reportUnusedFunction": "warning", // existing setting
}
""")
    (tmp_path / "pyproject.toml").write_text('[tool.pyright]\ntypeCheckingMode = "basic"\n')
    edit = configuration_edit(tmp_path)
    assert edit is not None
    assert edit["filename"] == str(path)
    assert "// retain inheritance and strict typing" in edit["text"]
    assert '"extends": "base.json"' in edit["text"]
    assert all(f'"{name}": false' in edit["text"] for name in RULES)
    assert str(tmp_path / "generated/**") in edit["text"]
    assert MIRRORS in edit["text"]
    path.write_text(edit["text"])
    assert configuration_edit(tmp_path) is None
    assert "basic" in (tmp_path / "pyproject.toml").read_text()


def test_json_handoff_handles_empty_and_nested_objects(tmp_path: Path) -> None:
    path = tmp_path / "pyrightconfig.json"

    for config in ({}, {"executionEnvironments": [{"root": "src", "extraPaths": ["types"]}]}):
        path.write_text(json.dumps(config))
        edit = configuration_edit(tmp_path)
        assert edit is not None
        expected = config | dict.fromkeys(RULES, False) | {"ignore": [MIRRORS]}
        assert json.loads(edit["text"]) == expected


def test_no_project_type_configuration_uses_editor_settings(tmp_path: Path) -> None:
    assert configuration_edit(tmp_path) is None
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    assert configuration_edit(tmp_path) is None


def test_configuration_edit_preserves_crlf_document_text(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    source = '[tool.pyright]\r\ntypeCheckingMode = "strict"\r\n'
    path.write_bytes(source.encode())
    edit = configuration_edit(tmp_path)
    assert edit is not None
    assert edit["source"] == source
    assert "\r\nreportUnusedFunction = false\r\n" in edit["text"]
    path.write_bytes(edit["text"].encode())
    assert configuration_edit(tmp_path) is None
