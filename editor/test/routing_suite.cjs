const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vscode = require("vscode");

async function until(predicate, label, timeout = 90000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    last = await predicate();
    if (last) return last;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`timeout waiting for ${typeof label === "function" ? label() : label}`);
}

function alive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) {
    if (error.code === "ESRCH") return false;
    throw error;
  }
}

const SOURCE = [
  "from pysx import Fragment, pysx",
  "",
  "",
  "def view() -> Fragment:",
  "    return pysx(t'div(class=\"wrap\"): \"hello\"')",
  "",
].join("\n");

exports.run = async function () {
  const extension = vscode.extensions.getExtension("pysx-local.pysx-lang");
  assert(extension, "fresh extension registered");
  const python = process.env.PYSX_EDITOR_PYTHON;
  const alternate = process.env.PYSX_EDITOR_ALTERNATE_PYTHON;
  const bare = process.env.PYSX_EDITOR_BARE_PYTHON;
  assert(python && alternate && bare, "three distinct environments supplied to the host");

  const roots = vscode.workspace.workspaceFolders;
  assert.equal(roots.length, 3, "three workspace roots");
  const folder = (name) => roots.find((item) => item.name === name);
  const consumer = folder("consumer");
  const secondary = folder("secondary");
  const nested = folder("nested");
  assert(consumer && secondary && nested, "named roots");

  const select = (target, value) => vscode.workspace.getConfiguration("pysx", target.uri)
    .update("pythonPath", value, vscode.ConfigurationTarget.WorkspaceFolder);

  await vscode.workspace.getConfiguration("pysx")
    .update("useLanguageServer", true, vscode.ConfigurationTarget.Workspace);
  await select(consumer, python);
  await select(secondary, alternate);
  await select(nested, python);

  const api = await extension.activate();
  assert(extension.isActive, "actual extension activation");
  assert(api?.router, "resident client exposes its workspace router");

  const statusOf = async (target) => {
    const owner = api.owner(target.uri);
    if (!owner?.lifecycle?.status().running) return null;
    try {
      const found = await owner.lifecycle.client.sendRequest("pysx/status", {});
      return found.workspaces.length === 1 && found.workspaces[0].pid ? found : null;
    } catch { return null; }
  };
  const ready = (target) => until(() => statusOf(target),
    () => `a ready server for ${target.name}: ${JSON.stringify(api.workspaces())}`);

  // A folder only builds analysis for documents it owns, so each root opens one.
  const open = async (target) => {
    const filename = path.join(target.uri.fsPath, "app.py");
    fs.writeFileSync(filename, SOURCE);
    const document = await vscode.workspace.openTextDocument(filename);
    await vscode.languages.setTextDocumentLanguage(document, "python");
    await vscode.window.showTextDocument(document);
    return document;
  };
  const documents = { consumer: await open(consumer), secondary: await open(secondary),
    nested: await open(nested) };

  // 1. Every folder runs its own server with the executable it selected.
  const first = { consumer: await ready(consumer), secondary: await ready(secondary),
    nested: await ready(nested) };
  assert.equal(first.consumer.interpreter, python);
  assert.equal(first.secondary.interpreter, alternate, "the folder's own executable is used");
  assert.equal(first.nested.interpreter, python);
  const pids = new Set(Object.values(first).map((item) => item.processId));
  assert.equal(pids.size, 3, "one distinct server process per folder");
  for (const [name, state] of Object.entries(first)) {
    assert.equal(fs.realpathSync.native(state.workspaces[0].root),
      fs.realpathSync.native(folder(name).uri.fsPath), `${name} analyses its own root`);
  }

  await until(async () => {
    const counts = await Promise.all(roots.map(async (target) =>
      (await statusOf(target))?.workspaces[0].openDocuments));
    return counts.every((count) => count === 1) ? counts : null;
  }, "each server holds exactly its own document");
  const symbols = await until(async () => {
    const found = await vscode.commands.executeCommand(
      "vscode.executeDocumentSymbolProvider", documents.nested.uri);
    return found && found.length ? found : null;
  }, "the nested document is served");
  assert.equal(symbols.filter((item) => item.name === "div").length, 1,
    "the innermost root serves the document exactly once");
  assert.equal(api.owner(documents.nested.uri).folder.name, "nested", "nearest owning folder");

  // 3. A settings change restarts only the folder it affects.
  await select(secondary, python);
  const switched = await until(async () => {
    const found = await statusOf(secondary);
    return found && found.interpreter === python ? found : null;
  }, "the changed folder adopts its new executable");
  assert.notEqual(switched.processId, first.secondary.processId);
  await until(() => !alive(first.secondary.processId), "the superseded server exits");
  assert.equal((await statusOf(consumer)).processId, first.consumer.processId,
    "an unaffected folder keeps its process");
  assert.equal((await statusOf(nested)).processId, first.nested.processId);

  // 4. The Python extension's own selection is honoured for that folder.
  await select(secondary, undefined);
  const environments = (await vscode.extensions.getExtension("ms-python.python").activate())
    .environments;
  await environments.updateActiveEnvironmentPath(alternate, secondary.uri);
  const api_selected = await until(async () => {
    const found = await statusOf(secondary);
    return found && found.interpreter === alternate ? found : null;
  }, "the API-selected environment is adopted");
  assert.notEqual(api_selected.processId, switched.processId);

  // 5. Rapid changes coalesce into the latest choice and one replacement.
  await select(secondary, bare);
  await select(secondary, alternate);
  await select(secondary, python);
  const coalesced = await until(async () => {
    const found = await statusOf(secondary);
    return found && found.interpreter === python ? found : null;
  }, "the latest of three rapid selections wins");
  assert.equal(api.owner(secondary.uri).error, null,
    "a superseded selection never leaves a failed state behind");
  assert.equal(api.owner(secondary.uri).lifecycle.status().starts, 1,
    "the surviving client started exactly once");
  void coalesced;

  // 6. A missing extra is reported and stays recoverable without reactivation.
  await select(secondary, bare);
  const unusable = await until(() => {
    const owner = api.owner(secondary.uri);
    return owner && !owner.lifecycle && owner.error ? owner : null;
  }, "the environment without the extra is reported");
  assert(unusable.error.includes("pysx[lsp]"), "the actionable extra is named");
  assert.equal((await statusOf(consumer)).processId, first.consumer.processId,
    "an unrelated folder stays usable");
  await select(secondary, python);
  const recovered = await ready(secondary);
  assert.equal(recovered.interpreter, python, "a later selection recovers the folder");

  // 7. The restart command resolves the executable afresh for its folder.
  await vscode.window.showTextDocument(documents.nested);
  await select(nested, alternate);
  await until(async () => {
    const found = await statusOf(nested);
    return found && found.interpreter === alternate ? found : null;
  }, "the nested folder adopts its new executable");
  const beforeRestart = await statusOf(nested);
  await vscode.commands.executeCommand("pysx.restartServer");
  const restarted = await until(async () => {
    const found = await statusOf(nested);
    return found && found.processId !== beforeRestart.processId ? found : null;
  }, "the restart command replaces the active folder's server");
  assert.equal(restarted.interpreter, alternate, "the restart resolves the selection afresh");
  assert.equal((await statusOf(consumer)).processId, first.consumer.processId,
    "the restart leaves other folders alone");

  // 8. A removed folder releases its client and its processes.
  const removed = await statusOf(secondary);
  const index = vscode.workspace.workspaceFolders.findIndex((item) => item.name === "secondary");
  assert(vscode.workspace.updateWorkspaceFolders(index, 1), "the folder is removed");
  const gonePath = fs.realpathSync.native(secondary.uri.fsPath);
  await until(() => !api.workspaces().some((item) => item.root === gonePath) &&
    !alive(removed.processId) && !alive(removed.workspaces[0].pid),
  "the removed folder's client, server and backend are released");
  assert.equal((await statusOf(consumer)).processId, first.consumer.processId,
    "removing a folder leaves the others running");

  await vscode.commands.executeCommand("workbench.action.closeAllEditors");
  console.log("PYSX EDITOR CASES PASSED: 8 (per-folder environments, nested ownership, " +
    "setting change, API selection, coalesced rapid changes, missing-extra recovery, " +
    "restart re-resolution and removed-folder disposal)");
};
