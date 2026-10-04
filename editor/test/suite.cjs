const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vscode = require("vscode");

async function until(predicate, label) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`timeout waiting for ${label}`);
}
exports.run = async function () {
  const extension = vscode.extensions.getExtension("pysx-local.pysx-lang");
  assert(extension, "fresh extension registered");
  await extension.activate();
  assert(extension.isActive, "actual extension activation");
  const pylance = vscode.extensions.getExtension("ms-python.vscode-pylance");
  assert(pylance, "actual Pylance registered");
  console.log(`VSCode ${vscode.version}; Pylance ${pylance.packageJSON.version}`);

  const filename = path.join(process.env.PYSX_EDITOR_WORKSPACE, "diagnostics.py");
  fs.writeFileSync(filename, 'from pysx import html\nvalue = html(t"""\n    div: "valid"\n""")\n');
  const document = await vscode.workspace.openTextDocument(filename);
  await vscode.languages.setTextDocumentLanguage(document, "python");
  await vscode.window.showTextDocument(document);
  const edit = new vscode.WorkspaceEdit();
  edit.replace(document.uri, new vscode.Range(2, 4, 2, 7), "Pge");
  assert(await vscode.workspace.applyEdit(edit));
  assert(await document.save(), "saved-file trigger");
  let diagnostic;
  await until(() => {
    diagnostic = vscode.languages.getDiagnostics(document.uri).find((item) =>
      item.source === "pysx" && item.message.includes("unknown component 'Pge'"));
    return Boolean(diagnostic);
  }, "saved-file checker diagnostic");
  assert.equal(diagnostic.range.start.line, 2);
  assert.equal(diagnostic.range.start.character, 4);
  // Closing a tab may retain its text model in VSCode's cache. Changing the
  // language closes the old Python document deterministically and exercises
  // the extension's real onDidCloseTextDocument subscription.
  await vscode.languages.setTextDocumentLanguage(document, "plaintext");
  await vscode.commands.executeCommand("workbench.action.closeAllEditors");
  await until(() => vscode.languages.getDiagnostics(document.uri).every((item) => item.source !== "pysx"), "diagnostic teardown");
  fs.unlinkSync(filename);
  console.log("PYSX EDITOR CASES PASSED: 3 (activation, saved diagnostics, Python document teardown)");
};
