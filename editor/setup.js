// One setup controller for both analysis paths.
//
// The Python ownership planner is the single policy owner: this module only observes
// settings, applies what the planner returns, retains the baseline and reverses it.
// Each mode supplies a small adapter naming the current document, project, interpreter
// and real replacement coverage; nothing else differs between them.

const vscode = require("vscode");
const { execFile } = require("node:child_process");
const trace = require("./trace.js");

// Exactly the keys pysx claims ownership of. The planner derives the values; this list
// only says where each one is observed and written.
const OWNED = [
  "python.analysis.ignore",
  "python.analysis.diagnosticSeverityOverrides",
  "python.analysis.disableTaggedHints",
  "python.analysis.fixAll",
  "editor.codeActionsOnSave",
  "ruff.lint.ignore",
];
const TARGET = vscode.ConfigurationTarget.WorkspaceFolder;

function split(key) {
  const index = key.indexOf(".");
  return [key.slice(0, index), key.slice(index + 1)];
}

function observe(resource) {
  const observed = {};
  for (const key of OWNED) {
    const [section, name] = split(key);
    observed[key] = vscode.workspace.getConfiguration(section, resource).get(name);
  }
  return observed;
}

async function write(resource, key, value) {
  const [section, name] = split(key);
  // The planner records an absent setting as null. Writing null would store an explicit
  // null override, so absence is restored by removing the key.
  await trace.observe("settings", "write", {
    resource: trace.resource(resource), key,
  }, () => vscode.workspace.getConfiguration(section, resource)
    .update(name, value === null ? undefined : value, TARGET));
}

function planner(python, command, root, request) {
  return new Promise((resolve, reject) => {
    const child = execFile(python, ["-m", "pysx.tooling.editor.config", command, root],
      { timeout: 30000, env: request.environment ?? process.env }, (error, stdout) => {
        if (error) reject(new Error(`ownership planning failed: ${error.message}`));
        else resolve(JSON.parse(stdout));
      });
    child.stdin.end(JSON.stringify({ ...request, environment: undefined }));
  });
}

const stateKey = (root) => `pysx.ownership:${root}`;

exports.OWNED = OWNED;
exports.observe = observe;
exports.stateKey = stateKey;

// Projection mirrors must never surface as their own documents. That exclusion is an
// owned setting like any other, so it is recorded in the baseline and reversed by
// restore rather than written silently behind the user's back.
exports.ownMirrorExclusion = async function (context, { folder, root, pattern }) {
  if (!folder) return false;
  const [section, name] = split("python.analysis.ignore");
  // The planner's convention: an absent setting is recorded as null, because restoring
  // it to `[]` would leave behind an explicit empty override the user never had.
  const observed = vscode.workspace.getConfiguration(section, folder.uri).get(name);
  const current = observed ?? [];
  if (current.includes(pattern)) return false;
  const key = stateKey(root);
  const existing = context.workspaceState.get(key);
  if (!existing?.values || !("python.analysis.ignore" in existing.values)) {
    await context.workspaceState.update(key, {
      values: { ...(existing?.values ?? {}), "python.analysis.ignore": observed ?? null },
      digests: existing?.digests ?? {},
    });
  }
  await write(folder.uri, "python.analysis.ignore", [...current, pattern]);
  return true;
};

const DISMISSED = (root) => `pysx.setupDismissed:${root}`;

// Integration is offered only once replacement analysis actually covers the rules it
// would silence, and only in a trusted workspace. A dismissal is remembered per project
// so activation never turns into a recurring prompt.
exports.offer = async function (context, { output, adapter, controller, prompt }) {
  if (vscode.workspace.isTrusted === false) return "untrusted";
  const selected = await adapter.resolve();
  if (selected.error || !selected.folder) return "unavailable";
  const { readiness } = selected;
  if (!readiness?.ready) return "not-ready";
  // Partial coverage never establishes readiness: silencing a rule nothing replaces is
  // exactly the outcome this gate exists to prevent.
  const coverage = readiness.coverage ?? {};
  if (!Object.keys(coverage).length || !Object.values(coverage).every(Boolean)) {
    return "uncovered";
  }
  const key = DISMISSED(readiness.root);
  if (context.workspaceState.get(key)) return "dismissed";
  const ask = prompt ?? vscode.window.showInformationMessage;
  const choice = await ask(
    `PYSX can configure ${selected.folder.name} so template-only imports survive ` +
    "organize-imports and unused-symbol checks. It changes a small set of workspace " +
    "folder settings, shows them first, and can be undone with PYSX: Restore Editor Settings.",
    "Set Up", "Not Now", "Never For This Folder");
  if (choice === "Never For This Folder") {
    await context.workspaceState.update(key, true);
    output.appendLine(`pysx: setup will not be offered again for ${selected.folder.name}.`);
    return "never";
  }
  if (choice !== "Set Up") return "declined";
  // Conditions are re-checked inside configure(), which resolves the adapter again
  // rather than trusting the state this prompt was raised from.
  return await controller.configure() ? "configured" : "failed";
};

exports.register = function (context, { output, adapter }) {
  const fail = (message) => {
    output.appendLine(message);
    output.show(true);
    void vscode.window.showWarningMessage(message);
    return false;
  };
  const note = (message) => {
    output.appendLine(message);
    void vscode.window.showInformationMessage(message);
    return true;
  };

  async function configure() {
    const selected = await adapter.resolve();
    if (selected.error) return fail(selected.error);
    const { folder, python, readiness, environment } = selected;
    if (!folder) {
      return fail("pysx: Open this file in a workspace folder before configuring " +
        "template-aware imports.");
    }
    try {
      if (!readiness.ready) {
        return fail("pysx: Workspace analysis is not ready yet" +
          `${readiness.error ? ` (${readiness.error})` : ""}. Wait for it to finish, then run ` +
          "Configure Template-Aware Imports again.");
      }
      const planned = await planner(python, "plan", readiness.root, {
        observed: observe(folder.uri),
        ready: readiness.ready,
        coverage: readiness.coverage,
        environment,
      });
      if (planned.refused) return fail(`pysx: ${planned.refused}`);

      if (planned.configuration) {
        const config = await vscode.workspace.openTextDocument(planned.configuration.filename);
        if (config.isDirty || config.getText() !== planned.configuration.source) {
          return fail("pysx: Save the project type configuration before configuring " +
            "template-aware imports.");
        }
        const edit = new vscode.WorkspaceEdit();
        edit.replace(config.uri,
          new vscode.Range(config.positionAt(0), config.positionAt(config.getText().length)),
          planned.configuration.text);
        if (!await vscode.workspace.applyEdit(edit) || !await config.save()) {
          return fail("pysx: Could not save the project type configuration. Setup is incomplete.");
        }
        output.appendLine(`Updated effective type settings in ${planned.configuration.filename}.`);
      }

      const applied = [];
      try {
        for (const step of planned.steps) {
          await write(folder.uri, step.key, step.value);
          applied.push(step);
        }
      } catch (error) {
        for (const step of planned.rollback) {
          if (applied.some((item) => item.key === step.key)) {
            await write(folder.uri, step.key, step.value).catch(() => {});
          }
        }
        return fail(`pysx: Setup was rolled back after a failed write: ${error.message ?? error}`);
      }

      // Repeated setup must still restore what the user originally had, so the first
      // baseline is never overwritten by a later, already-owned one.
      // Merge per key rather than per baseline: the mirror exclusion may already have
      // recorded one key's original, and that earlier value must survive while the keys
      // it never covered still get theirs. Taking either whole object loses one of them.
      const key = stateKey(readiness.root);
      const existing = context.workspaceState.get(key);
      await context.workspaceState.update(key, {
        values: { ...planned.state.values, ...(existing?.values ?? {}) },
        digests: planned.state.digests,
      });
      for (const item of planned.notes) output.appendLine(item);
      return note(`pysx: Template-aware imports configured for ${folder.name}.`);
    } catch (error) {
      return fail(`pysx: Could not configure template-aware imports: ${error.message ?? error}`);
    }
  }

  async function restore() {
    const selected = await adapter.resolve();
    if (selected.error) return fail(selected.error);
    const { folder, python, readiness, environment } = selected;
    if (!folder) return fail("pysx: Open this file in a workspace folder before restoring.");
    try {
      // The adapter names the analyzed root, and setup retained the baseline under that
      // same name; deriving it again here could disagree.
      const root = readiness.root;
      const retained = context.workspaceState.get(stateKey(root));
      if (!retained) return fail(`pysx: pysx owns no editor settings in ${folder.name}.`);
      const planned = await planner(python, "restore", root,
        { observed: observe(folder.uri), state: retained, environment });
      for (const step of planned.steps) await write(folder.uri, step.key, step.value);
      await context.workspaceState.update(stateKey(root), undefined);
      for (const key of planned.skipped) {
        output.appendLine(`pysx: ${key} changed after pysx wrote it and was left as it is.`);
      }
      return note(`pysx: Restored ${planned.steps.length} editor settings in ${folder.name}` +
        `${planned.skipped.length ? `; ${planned.skipped.length} were edited later and kept` : ""}.`);
    } catch (error) {
      return fail(`pysx: Could not restore editor settings: ${error.message ?? error}`);
    }
  }

  const traced = (name, action) => (...args) =>
    trace.observe("command", name, {}, () => action(...args));
  context.subscriptions.push(
    vscode.commands.registerCommand("pysx.configureTooling", traced("configure", configure)),
    vscode.commands.registerCommand("pysx.restoreTooling", traced("restore", restore)),
  );
  return { configure, restore };
};
