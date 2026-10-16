const vscode = require("vscode");
const { execFile } = require("node:child_process");

// Exactly the keys pysx claims ownership of. The Python planner derives the
// values; this list only says where each one is observed and written.
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
  // The planner records an absent setting as null. Writing null would store an
  // explicit null override, so absence is restored by removing the key.
  await vscode.workspace.getConfiguration(section, resource)
    .update(name, value === null ? undefined : value, TARGET);
}

function planner(python, command, root, request) {
  return new Promise((resolve, reject) => {
    const child = execFile(python, ["-m", "pysx.editor_config", command, root],
      { timeout: 30000 }, (error, stdout) => {
        if (error) reject(new Error(`ownership planning failed: ${error.message}`));
        else resolve(JSON.parse(stdout));
      });
    child.stdin.end(JSON.stringify(request));
  });
}

exports.register = function (context, { output, router }) {
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
  function active() {
    const document = vscode.window.activeTextEditor?.document;
    if (!document || document.languageId !== "python" || document.uri.scheme !== "file") {
      return { error: "pysx: Open a Python file before running this command." };
    }
    const owner = router.owner(document.uri);
    if (!owner?.lifecycle?.status().running) {
      return { error: owner?.error ?? "pysx: No language server is running for this file yet." };
    }
    return { document, owner };
  }
  const stateKey = (root) => `pysx.ownership:${root}`;

  async function configure() {
    const selected = active();
    if (selected.error) return fail(selected.error);
    const { document, owner } = selected;
    const folder = owner.folder;
    if (!folder) {
      return fail("pysx: Open this file in a workspace folder before configuring " +
        "template-aware imports.");
    }

    try {
      const readiness = await owner.lifecycle.client.sendRequest("pysx/readiness",
        { uri: document.uri.toString() });
      if (!readiness.ready) {
        return fail("pysx: Workspace analysis is not ready yet" +
          `${readiness.error ? ` (${readiness.error})` : ""}. Wait for it to finish, then run ` +
          "Configure Template-Aware Imports again.");
      }
      const planned = await planner(owner.python, "plan", readiness.root, {
        observed: observe(folder.uri),
        ready: readiness.ready,
        coverage: readiness.coverage,
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

      // Repeated setup must still restore what the user originally had, so the
      // first baseline is never overwritten by a later, already-owned one.
      const key = stateKey(readiness.root);
      const existing = context.workspaceState.get(key);
      await context.workspaceState.update(key, existing
        ? { values: existing.values, digests: planned.state.digests }
        : planned.state);
      for (const item of planned.notes) output.appendLine(item);
      return note(`pysx: Template-aware imports configured for ${folder.name}.`);
    } catch (error) {
      return fail(`pysx: Could not configure template-aware imports: ${error.message ?? error}`);
    }
  }

  async function restore() {
    const selected = active();
    if (selected.error) return fail(selected.error);
    const { owner } = selected;
    const folder = owner.folder;
    if (!folder) return fail("pysx: Open this file in a workspace folder before restoring.");

    try {
      // The server names the analyzed root, and setup retained the baseline
      // under that same name; deriving it again here could disagree.
      const { root } = await owner.lifecycle.client.sendRequest("pysx/readiness",
        { uri: selected.document.uri.toString() });
      const retained = context.workspaceState.get(stateKey(root));
      if (!retained) return fail(`pysx: pysx owns no editor settings in ${folder.name}.`);
      const planned = await planner(owner.python, "restore", root,
        { observed: observe(folder.uri), state: retained });
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

  async function rename(requested) {
    const selected = active();
    if (selected.error) return fail(selected.error);
    const { document } = selected;
    const position = vscode.window.activeTextEditor.selection.active;
    const name = requested ?? await vscode.window.showInputBox({
      prompt: "Rename Python symbol and matching markup",
      validateInput: (value) => /^[A-Za-z_]\w*$/.test(value) ? null : "Enter a Python identifier",
    });
    if (!name) return false;
    const edit = await vscode.commands.executeCommand(
      "vscode.executeDocumentRenameProvider", document.uri, position, name);
    if (!edit || !edit.size) return fail("pysx: That symbol could not be renamed safely.");
    return vscode.workspace.applyEdit(edit);
  }

  async function refresh() {
    const selected = active();
    if (selected.error) return fail(selected.error);
    await selected.owner.lifecycle.client.sendRequest("pysx/refresh",
      { uri: selected.document.uri.toString() });
    return true;
  }

  context.subscriptions.push(
    vscode.commands.registerCommand("pysx.configureTooling", configure),
    vscode.commands.registerCommand("pysx.restoreTooling", restore),
    vscode.commands.registerCommand("pysx.rename", rename),
    vscode.commands.registerCommand("pysx.refresh", refresh),
    vscode.window.onDidChangeActiveTextEditor((editor) =>
      vscode.commands.executeCommand("setContext", "pysx.managedDocument",
        editor?.document?.languageId === "python" && editor.document.uri.scheme === "file")),
  );
  void vscode.commands.executeCommand("setContext", "pysx.managedDocument",
    vscode.window.activeTextEditor?.document?.languageId === "python");

  return { configure, restore, rename, refresh, owned: OWNED };
};
