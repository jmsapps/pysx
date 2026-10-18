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
  throw new Error(`timeout waiting for ${label}`);
}

const LIBRARY = [
  "from pysx import Fragment, pysx",
  "",
  "",
  "def Card(*, title: str) -> Fragment:",
  "    return pysx(t'p: {title}')",
  "",
  "",
  "def Spare(*, title: str) -> Fragment:",
  "    return pysx(t'p: {title}')",
  "",
].join("\n");

// `Card` is used only inside markup; `Spare` is genuinely dead. Both must be
// classified correctly or the whole ownership contract is worthless.
const CONSUMER = [
  "from pysx import Fragment, pysx",
  "from library import Card, Spare",
  "",
  "",
  "def view() -> Fragment:",
  "    return pysx(t'Card(title=\"hello\")')",
  "",
].join("\n");

const PLAIN = [
  "import json",
  "import os",
  "",
  "",
  "def plain() -> str:",
  "    return json.dumps({})",
  "",
].join("\n");

exports.run = async function () {
  const suite = process.env.PYSX_EDITOR_SUITE;
  const extension = vscode.extensions.getExtension("pysx-local.pysx-lang");
  assert(extension, "fresh extension registered");
  const python = process.env.PYSX_EDITOR_PYTHON;
  assert(python, "selected interpreter supplied to the host");

  const settings = vscode.workspace.getConfiguration("pysx");
  await settings.update("pythonPath", python, vscode.ConfigurationTarget.Workspace);
  await settings.update("useLanguageServer", true, vscode.ConfigurationTarget.Workspace);
  const pylance = vscode.extensions.getExtension("ms-python.vscode-pylance");
  assert(pylance, "actual Pylance registered");
  const activationOrder = process.env.PYSX_EDITOR_ACTIVATION_ORDER ?? "client-first";
  assert(["client-first", "pylance-first"].includes(activationOrder), "known activation order");
  if (activationOrder === "pylance-first") await pylance.activate();
  const api = await extension.activate();
  assert(extension.isActive, "actual extension activation");
  assert(api && api.client, "resident client activated");

  const ruff = vscode.extensions.getExtension("charliermarsh.ruff");
  assert(ruff, "actual Ruff extension registered");
  if (activationOrder === "client-first") await pylance.activate();
  await ruff.activate();
  console.log(
    `VSCode ${vscode.version}; Pylance ${pylance.packageJSON.version}; ` +
    `Ruff ${ruff.packageJSON.version}; ${activationOrder}`);

  const root = vscode.workspace.workspaceFolders[0].uri.fsPath;
  fs.writeFileSync(path.join(root, "library.py"), LIBRARY);
  fs.writeFileSync(path.join(root, "view.py"), CONSUMER);
  fs.writeFileSync(path.join(root, "plain.py"), PLAIN);

  const open = async (name) => {
    const document = await vscode.workspace.openTextDocument(path.join(root, name));
    await vscode.languages.setTextDocumentLanguage(document, "python");
    await vscode.window.showTextDocument(document);
    return document;
  };
  const library = await open("library.py");
  const consumer = await open("view.py");
  void library;

  const pysxOf = (uri) =>
    vscode.languages.getDiagnostics(uri).filter((item) => item.source === "pysx");

  const configureTooling = async () => {
    // Buffer diagnostics can precede the complete workspace snapshot required
    // by setup. Repeated setup can also follow a configuration invalidation.
    const uri = vscode.window.activeTextEditor.document.uri.toString();
    await until(async () => {
      const readiness = await api.client.sendRequest("pysx/readiness", { uri });
      assert.equal(readiness.error, null, "workspace analysis must be healthy before setup");
      return readiness.ready;
    }, "complete workspace analysis before setup");
    return vscode.commands.executeCommand("pysx.configureTooling");
  };

  // 1. The template-only import is never reported dead.
  const dead = await until(() => {
    const found = pysxOf(consumer.uri).filter((item) => String(item.code) === "F401");
    return found.length ? found : null;
  }, "the genuinely unused import is reported");
  assert.equal(dead.length, 1, "exactly one dead import");
  assert(consumer.getText(dead[0].range).includes("Spare"), "the dead import is Spare, not Card");
  assert(!pysxOf(consumer.uri).some((item) =>
    String(item.code) === "reportUnusedImport"), "no mirror unused-import diagnostic");

  // 2. An ordinary non-template file in the same project still behaves.
  const plain = await open("plain.py");
  await until(() => pysxOf(plain.uri).some((item) => String(item.code) === "F401"),
    "ordinary file reports its own unused import");
  assert(pysxOf(plain.uri).some((item) => plain.getText(item.range).includes("os")),
    "the ordinary file's dead import is named");

  // 3. Removing the last tag use turns a live import into a dead one.
  const edit = new vscode.WorkspaceEdit();
  const line = CONSUMER.split("\n").findIndex((text) => text.includes("Card(title="));
  edit.replace(consumer.uri, new vscode.Range(line, 0, line, CONSUMER.split("\n")[line].length),
    "    return pysx(t'p: \"plain\"')");
  assert(await vscode.workspace.applyEdit(edit));
  await until(() => pysxOf(consumer.uri).filter((item) =>
    String(item.code) === "F401").length === 2, "removing the last use reports both imports dead");
  assert(consumer.isDirty, "the change was never saved");

  // 4. No private mirror ever surfaces as its own document.
  const everyUri = vscode.languages.getDiagnostics().map(([uri]) => uri.fsPath);
  assert(!everyUri.some((item) => item.includes("_pysx_revision_")),
    "a private projection mirror leaked into the problems view");

  let cases = 4;
  if (suite === "editor_surface") {
    // 5-7. The advertised surface answers through the real client.
    const symbols = await until(async () => {
      const found = await vscode.commands.executeCommand(
        "vscode.executeDocumentSymbolProvider", library.uri);
      return found && found.length ? found : null;
    }, "document symbols through the real client");
    assert(symbols.length, "markup symbols served");
    assert.equal(api.client.initializeResult.capabilities.semanticTokensProvider, undefined,
      "resident markup does not compete with Pylance semantic highlighting");
    await require("./client_suite.cjs").pythonSemantics(library,
      [["Card", "function"], ["title", "parameter", "{title}"]]);
    const hover = await until(async () => {
      const at = CONSUMER.indexOf("Card", CONSUMER.indexOf("pysx(t'"));
      const found = await vscode.commands.executeCommand(
        "vscode.executeHoverProvider", consumer.uri, consumer.positionAt(at));
      return found && found.length ? found : null;
    }, "hover through the real client");
    assert(hover.length, "hover served");

    // 8. Real setup writes exactly the owned settings at the folder target.
    // Case 3 removed the only tag use; restore it so the replacement action is
    // judged against a live template-only import again.
    const restoreUse = new vscode.WorkspaceEdit();
    restoreUse.replace(consumer.uri, new vscode.Range(line, 0, line, consumer.lineAt(line).text.length),
      CONSUMER.split("\n")[line]);
    assert(await vscode.workspace.applyEdit(restoreUse));
    await until(() => pysxOf(consumer.uri).filter((item) =>
      String(item.code) === "F401").length === 1, "the template-only import is live again");
    const folder = vscode.workspace.getWorkspaceFolder(consumer.uri);
    const owned = (key) => {
      const index = key.indexOf(".");
      return vscode.workspace.getConfiguration(key.slice(0, index), folder.uri)
        .inspect(key.slice(index + 1));
    };
    assert.equal(owned("editor.codeActionsOnSave").workspaceFolderValue, undefined,
      "setup starts from an unconfigured workspace folder");
    assert.equal(await configureTooling(), true,
      "the advertised setup command is registered and succeeds");
    const actions = owned("editor.codeActionsOnSave").workspaceFolderValue;
    assert.equal(actions["source.organizeImports.pysx"], "explicit",
      "the replacement action is enabled");
    for (const disabled of ["source.fixAll", "source.fixAll.ruff", "source.organizeImports",
      "source.organizeImports.ruff", "source.unusedImports"]) {
      assert.equal(actions[disabled], "never", `${disabled} is disabled by setup`);
    }
    assert(owned("ruff.lint.ignore").workspaceFolderValue.includes("F401"),
      "the competing lexical rule is silenced");
    assert.equal(owned("python.analysis.disableTaggedHints").workspaceFolderValue, true);

    // 9. Repeated setup is a no-op that preserves the first baseline.
    assert.equal(await configureTooling(), true,
      "repeated setup succeeds");
    assert.deepEqual(owned("editor.codeActionsOnSave").workspaceFolderValue, actions,
      "repeated setup changes nothing");

    // 10. The enabled replacement actually removes only the genuinely dead import.
    const offered = await until(async () => {
      const found = await vscode.commands.executeCommand("vscode.executeCodeActionProvider",
        consumer.uri, new vscode.Range(0, 0, 0, 0), "source.organizeImports.pysx");
      return found && found.length ? found : null;
    }, "the resident organize-imports action");
    assert.equal(offered.length, 1, "exactly one replacement action");
    assert(await vscode.workspace.applyEdit(offered[0].edit), "the replacement edit applies");
    assert(consumer.getText().includes("Card"), "the template-only import survives");
    assert(!consumer.getText().includes("Spare"), "the genuinely unused import is removed");
    assert.equal(await vscode.commands.executeCommand("pysx.refresh"), true,
      "the advertised refresh command is registered and answers");

    // 11. Restoration returns ownership and respects a later user edit.
    await vscode.workspace.getConfiguration("python", folder.uri)
      .update("analysis.disableTaggedHints", false, vscode.ConfigurationTarget.WorkspaceFolder);
    assert.equal(await vscode.commands.executeCommand("pysx.restoreTooling"), true,
      "the advertised restoration command is registered and succeeds");
    assert.deepEqual(owned("editor.codeActionsOnSave").workspaceFolderValue, {},
      "the owned action settings are given back to their original value");
    assert.equal(owned("ruff.lint.ignore").workspaceFolderValue, undefined,
      "a setting that was absent before setup is removed again");
    assert.equal(owned("python.analysis.disableTaggedHints").workspaceFolderValue, false,
      "a value the user changed after setup is kept rather than overwritten");
    cases = 11;
  } else {
    cases = 7;
    // 5. CLI and editor agree about which import is dead.
    const { execFileSync } = require("node:child_process");
    const reported = JSON.parse(execFileSync(python,
      ["-m", "pysx.check", path.join(root, "plain.py")], { encoding: "utf8" }));
    assert(Array.isArray(reported), "the CLI returned a diagnostic list");
    // Nothing may be advertised in the manifest without something answering it.
    const manifest = JSON.parse(
      fs.readFileSync(path.join(extension.extensionPath, "package.json"), "utf8"));
    const registered = new Set(await vscode.commands.getCommands(true));
    for (const item of manifest.contributes.commands) {
      assert(registered.has(item.command), `${item.command} is advertised and registered`);
    }
    assert.equal(await configureTooling(), true,
      "the setup command runs against this workspace");
  }

  await vscode.commands.executeCommand("workbench.action.closeAllEditors");
  console.log(`PYSX EDITOR CASES PASSED: ${cases} (template-only import live, genuine dead ` +
    `import reported, ordinary file covered, last-use removal, no mirror leak` +
    (suite === "editor_surface"
      ? ", resident surface, setup, repeated setup, replacement action, restoration)"
      : ", CLI agreement, every advertised command registered, setup)"));
};
