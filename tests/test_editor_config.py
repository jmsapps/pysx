"""Project settings take precedence; configuration edits preserve other settings."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest

from pysx.editor_config import MIRRORS, RULES, configuration_edit
from pysx.editor_ownership import (
    PROJECTION_GLOB,
    RUFF_IGNORED,
    SUPPRESSED,
    Plan,
    RetainedState,
    SetupRefusedError,
    desired,
    ownership_plan,
    restore_plan,
)

COVERED = {"ruff": True, "types": True}
ROOT = Path(__file__).resolve().parents[1]


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


def observed_after(plan: Plan, observed: dict[str, object]) -> dict[str, object]:
    out = dict(observed)

    for step in plan.steps:
        out[step.key] = step.value

    return out


def full_plan(root: Path, observed: dict[str, object] | None = None) -> Plan:
    return ownership_plan(
        observed if observed is not None else {},
        root,
        coverage={"ruff": True, "types": True},
        ready=True,
    )


def test_edits_formatting_setup_takes_only_the_owned_settings(tmp_path: Path) -> None:
    plan = full_plan(tmp_path, {"python.analysis.ignore": ["vendor/**"]})
    changed = cast("dict[str, Any]", {step.key: step.value for step in plan.steps})
    assert changed["python.analysis.ignore"] == ["vendor/**", PROJECTION_GLOB]
    assert changed["python.analysis.diagnosticSeverityOverrides"] == dict.fromkeys(
        SUPPRESSED, "none"
    )
    assert changed["editor.codeActionsOnSave"]["source.organizeImports.pysx"] == "explicit"
    assert set(changed["ruff.lint.ignore"]) == set(RUFF_IGNORED)
    assert "reportMissingImports" not in str(changed)


def test_edits_formatting_setup_preserves_unrelated_user_values(tmp_path: Path) -> None:
    observed: dict[str, object] = {
        "python.analysis.diagnosticSeverityOverrides": {"reportMissingImports": "error"},
        "editor.codeActionsOnSave": {"source.sortImports": "explicit"},
        "ruff.lint.ignore": ["E501"],
        "python.analysis.fixAll": ["source.unusedImports", "source.convertImportFormat"],
    }
    changed = cast(
        "dict[str, Any]", {step.key: step.value for step in full_plan(tmp_path, observed).steps}
    )
    assert changed["python.analysis.diagnosticSeverityOverrides"]["reportMissingImports"] == "error"
    assert changed["editor.codeActionsOnSave"]["source.sortImports"] == "explicit"
    assert "E501" in changed["ruff.lint.ignore"]
    assert changed["python.analysis.fixAll"] == ["source.convertImportFormat"]


def test_edits_formatting_setup_normalizes_a_cleared_ruff_ignore_list(tmp_path: Path) -> None:
    changed = cast(
        "dict[str, Any]",
        {step.key: step.value for step in full_plan(tmp_path, {"ruff.lint.ignore": None}).steps},
    )
    assert changed["ruff.lint.ignore"] == list(RUFF_IGNORED)


def test_edits_formatting_setup_is_idempotent(tmp_path: Path) -> None:
    first = full_plan(tmp_path)
    settled = observed_after(first, {})
    second = full_plan(tmp_path, settled)
    assert second.steps == ()
    assert len(second.notes) == len(first.steps)


def test_edits_formatting_setup_refuses_without_replacement_coverage(tmp_path: Path) -> None:
    with pytest.raises(SetupRefusedError) as refusal:
        ownership_plan({}, tmp_path, coverage={"ruff": True, "types": False}, ready=True)
    assert "reportUnusedFunction" in str(refusal.value)
    assert "reportUnusedImport" not in str(refusal.value)


def test_edits_formatting_setup_refuses_until_analysis_is_ready(tmp_path: Path) -> None:
    with pytest.raises(SetupRefusedError) as refusal:
        ownership_plan({}, tmp_path, coverage={"ruff": True, "types": True}, ready=False)
    assert "not ready" in str(refusal.value)


def test_edits_formatting_rollback_restores_an_interrupted_apply(tmp_path: Path) -> None:
    observed: dict[str, object] = {"ruff.lint.ignore": ["E501"], "python.analysis.ignore": []}
    plan = full_plan(tmp_path, observed)
    partial = dict(observed)

    for step in plan.steps[:3]:
        partial[step.key] = step.value

    for step in plan.rollback:
        if step.key in partial:
            partial[step.key] = step.value
    assert {key: partial[key] for key in observed} == observed


def test_edits_formatting_restoration_returns_every_owned_setting(tmp_path: Path) -> None:
    observed: dict[str, object] = {"ruff.lint.ignore": ["E501"]}
    plan = full_plan(tmp_path, observed)
    settled = observed_after(plan, observed)
    steps, skipped = restore_plan(settled, plan.state)
    assert skipped == ()
    restored = dict(settled)

    for step in steps:
        restored[step.key] = step.value
    assert restored["ruff.lint.ignore"] == ["E501"]
    assert (
        restored["python.analysis.diagnosticSeverityOverrides"] is None
        or not restored["python.analysis.diagnosticSeverityOverrides"]
    )


def test_edits_formatting_restoration_refuses_keys_the_user_edited(tmp_path: Path) -> None:
    plan = full_plan(tmp_path)
    settled = observed_after(plan, {})
    settled["ruff.lint.ignore"] = ["F401", "W605"]
    steps, skipped = restore_plan(settled, plan.state)
    assert skipped == ("ruff.lint.ignore",)
    assert "ruff.lint.ignore" not in {step.key for step in steps}
    assert "python.analysis.disableTaggedHints" in {step.key for step in steps}


def test_edits_formatting_setup_cli_round_trips_plan_and_restore(tmp_path: Path) -> None:
    def run(command: str, payload: dict[str, object]) -> dict[str, Any]:
        result = subprocess.run(
            [sys.executable, "-m", "pysx.editor_config", command, str(tmp_path)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=True,
        )

        return cast("dict[str, Any]", json.loads(result.stdout))

    planned = run(
        "plan", {"observed": {}, "ready": True, "coverage": {"ruff": True, "types": True}}
    )
    assert "refused" not in planned
    settled = {step["key"]: step["value"] for step in planned["steps"]}
    restored = run("restore", {"observed": settled, "state": planned["state"]})
    assert restored["skipped"] == []
    assert {step["key"] for step in restored["steps"]} == set(settled)

    refused = run(
        "plan", {"observed": {}, "ready": False, "coverage": {"ruff": True, "types": True}}
    )
    assert "not ready" in refused["refused"]


def test_resident_commands_client_owns_exactly_the_planned_settings() -> None:
    """The client may not write a key the planner does not own, or miss one."""
    source = (ROOT / "editor" / "commands.js").read_text()
    listed = source.split("const OWNED = [", 1)[1].split("];", 1)[0]
    owned = [line.strip().strip('",') for line in listed.splitlines() if '"' in line]
    assert owned == sorted(desired({}), key=owned.index)
    assert set(owned) == set(desired({}))


def test_resident_commands_repeated_setup_keeps_the_original_baseline(tmp_path: Path) -> None:
    """A second setup run must still restore what the user originally had."""
    baseline = {"source.fixAll": "explicit"}
    original: dict[str, object] = {"editor.codeActionsOnSave": baseline}
    first = ownership_plan(original, tmp_path, coverage=COVERED, ready=True)
    applied: dict[str, object] = dict(original)
    applied.update({step.key: step.value for step in first.steps})
    second = ownership_plan(applied, tmp_path, coverage=COVERED, ready=True)
    assert second.steps == ()
    retained = RetainedState(values=first.state["values"], digests=second.state["digests"])
    steps, skipped = restore_plan(applied, retained)
    assert skipped == ()
    assert {step.key: step.value for step in steps}["editor.codeActionsOnSave"] == baseline
