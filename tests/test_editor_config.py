"""Project settings take precedence; configuration edits preserve other settings."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest

from pysx.tooling.editor.config import MIRRORS, RULES, configuration_edit, parse_json_configuration
from pysx.tooling.editor.ownership import (
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
    path.write_bytes(source.encode("utf-8"))
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
    path.write_bytes(edit["text"].encode("utf-8"))
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
    assert str(tmp_path / "generated/**") in cast(
        "list[str]", parse_json_configuration(edit["text"])["ignore"]
    )
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
            [sys.executable, "-m", "pysx.tooling.editor.config", command, str(tmp_path)],
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
    # One controller owns the key list for both analysis modes.
    source = (ROOT / "editor" / "setup.js").read_text()
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


SHARED_SETUP_PROBE = """
const assert = require("node:assert/strict");
const path = require("node:path");
const Module = require("node:module");
const editor = process.argv[1];

// A fake host: enough VSCode surface for the shared controller, and nothing mode
// specific, so anything the controller needs must come from its adapter.
const settings = new Map();
const registered = new Map();
const stored = new Map();
const section = (name, resource) => ({
  get: (key, fallback) =>
    settings.has(`${name}.${key}`) ? settings.get(`${name}.${key}`) : fallback,
  update: async (key, value) => {
    if (value === undefined) settings.delete(`${name}.${key}`);
    else settings.set(`${name}.${key}`, value);
  },
});
const vscode = {
  ConfigurationTarget: { WorkspaceFolder: 3 },
  Uri: { file: (value) => ({ fsPath: value, toString: () => `file://${value}` }) },
  workspace: {
    isTrusted: true,
    getConfiguration: section,
    openTextDocument: async () => { throw new Error("unused"); },
  },
  commands: {
    registerCommand: (name, handler) => {
      registered.set(name, handler);
      return { dispose() {} };
    },
  },
  window: { showWarningMessage() {}, showInformationMessage() {} },
};
const original = Module.prototype.require;
Module.prototype.require = function (name) {
  if (name === "vscode") return vscode;
  return original.apply(this, arguments);
};
const setup = require(path.join(editor, "setup.js"));
Module.prototype.require = original;

const context = { subscriptions: [], workspaceState: {
  get: (key) => stored.get(key), update: async (key, value) => {
    if (value === undefined) stored.delete(key); else stored.set(key, value);
  } } };
const output = { appendLine() {}, show() {} };
const folder = { name: "app", uri: vscode.Uri.file(process.argv[3]) };

const adapter = { resolve: async () => ({
  document: { uri: folder.uri }, folder, python: process.argv[2],
  readiness: { ready: true, root: process.argv[3], coverage: { ruff: true, types: true } },
}) };
const controller = setup.register(context, { output, adapter });

(async () => {
  assert(registered.has("pysx.configureTooling"), "setup registers configure once");
  assert(registered.has("pysx.restoreTooling"), "setup registers restore once");

  // A user value that setup must preserve and later give back untouched.
  settings.set("ruff.lint.ignore", ["E501"]);
  const before = new Map(settings);

  // The projection exclusion is an owned, recorded change rather than a silent write.
  await setup.ownMirrorExclusion(context, { folder, root: process.argv[3],
    pattern: "**/_pysx_revision_*/**" });
  assert(settings.get("python.analysis.ignore").includes("**/_pysx_revision_*/**"),
    "the mirror exclusion is applied");

  assert.equal(await controller.configure(), true, "setup applies the planned configuration");
  assert.notDeepEqual(settings.get("ruff.lint.ignore"), before.get("ruff.lint.ignore"));

  // Repeating setup must not promote already-owned values into the baseline.
  assert.equal(await controller.configure(), true, "repeated setup succeeds");
  assert.equal(await controller.restore(), true, "restore reverses what setup owned");

  for (const key of setup.OWNED) {
    assert.deepEqual(settings.get(key), before.get(key),
      `${key} returned to its original value, got ${JSON.stringify(settings.get(key))}`);
  }
  assert.equal(await controller.restore(), false, "nothing remains owned after restoration");

  // The offer is gated on genuine replacement coverage and remembers a refusal.
  const asked = [];
  const prompt = (message, ...choices) => { asked.push(message); return choices[0]; };
  const silent = () => { throw new Error("the offer must not prompt here"); };

  const partial = { resolve: async () => ({
    folder, python: process.argv[2],
    readiness: { ready: true, root: process.argv[3], coverage: { ruff: true, types: false } },
  }) };
  assert.equal(await setup.offer(context, { output, adapter: partial, controller,
    prompt: silent }), "uncovered", "partial coverage never establishes readiness");

  const unready = { resolve: async () => ({
    folder, python: process.argv[2], readiness: { ready: false, root: process.argv[3] },
  }) };
  assert.equal(await setup.offer(context, { output, adapter: unready, controller,
    prompt: silent }), "not-ready");

  assert.equal(await setup.offer(context, { output, adapter, controller,
    prompt: (message, ...choices) => { asked.push(message); return choices[2]; } }), "never",
    "the user can refuse permanently");
  assert.equal(await setup.offer(context, { output, adapter, controller, prompt: silent }),
    "dismissed", "a remembered refusal is never re-prompted");
  assert.equal(asked.length, 1, "exactly one prompt was raised");
  assert(asked[0].includes("Restore Editor Settings"), "the prompt explains how to undo it");

  vscode.workspace.isTrusted = false;
  assert.equal(await setup.offer(context, { output, adapter: partial, controller,
    prompt: silent }), "untrusted", "an untrusted workspace is never provisioned");
  vscode.workspace.isTrusted = true;
  console.log("SHARED SETUP OK");
})().catch((error) => { console.error(error.message); process.exitCode = 1; });
"""


def test_shared_setup_controller_is_reversible_for_any_adapter(tmp_path: Path) -> None:
    """One controller, one policy owner, and a baseline that survives repeated setup."""
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "0"\n')
    result = subprocess.run(
        ["node", "-e", SHARED_SETUP_PROBE, str(root / "editor"), sys.executable, str(tmp_path)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SHARED SETUP OK" in result.stdout


def test_shared_setup_policy_has_one_owner_in_both_modes() -> None:
    """Neither adapter may carry its own copy of the settings recipe."""
    editor = Path(__file__).resolve().parents[1] / "editor"
    worker = (editor / "authoring.js").read_text(encoding="utf-8")
    resident = (editor / "commands.js").read_text(encoding="utf-8")

    for name, source in (("authoring.js", worker), ("commands.js", resident)):
        assert "setup.js" in source or "./setup.js" in source, f"{name} uses the shared controller"
        assert "reportUnusedImport" not in source, f"{name} still carries a duplicate rule list"
        assert "source.organizeImports" not in source, f"{name} still writes save actions directly"
        assert 'registerCommand("pysx.configureTooling"' not in source, (
            f"{name} registers setup separately"
        )
