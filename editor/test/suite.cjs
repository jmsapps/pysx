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

  if (process.env.PYSX_EDITOR_SUITE === "packaging") {
    // Compare against this checkout's real root; a hardcoded path fragment would
    // pass vacuously on any other machine or CI runner.
    const checkout = (process.env.PYSX_EDITOR_CHECKOUT ?? "").replace(/[\\/]+$/, "");
    assert(checkout && path.isAbsolute(checkout), "checkout root supplied to the host");
    assert(!fs.existsSync(path.join(extension.extensionPath, "pysx-root.json")), "portable artifact excludes checkout metadata");
    const source = fs.readFileSync(path.join(extension.extensionPath, "extension.js"), "utf8");
    assert(!source.includes(checkout) && !source.includes("pysx-root.json"), "no baked checkout path");
    await vscode.workspace.getConfiguration("pysxProof").update("python", process.env.PYSX_EDITOR_PYTHON, vscode.ConfigurationTarget.Workspace);
    const result = await vscode.commands.executeCommand("pysxProof.launch");
    assert(result, "actual child stdio response");
    assert.equal(result.serverInfo.name, "pysx-architecture-proof");
    assert.equal(result.proof.transport, "stdio");
    assert.equal(result.proof.isolated, true);
    assert.equal(result.proof.shutdown, true);
    assert(result.proof.executable.startsWith(path.dirname(process.env.PYSX_EDITOR_PYTHON)), "selected installed interpreter used");
    assert(!result.proof.sys_path.some(
      (item) => item === checkout || item.startsWith(checkout + path.sep)),
      "no checkout import path");
    console.log("PYSX EDITOR CASES PASSED: 3 (portable activation, installed isolated stdio initialize, clean shutdown)");
    return;
  }

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
