const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vscode = require("vscode");

async function until(fn, label) {
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    const result = await fn();
    if (result) return result;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`timeout waiting for ${label}`);
}
exports.run = async function () {
  await vscode.extensions.getExtension("ms-python.python").activate();
  await vscode.workspace.getConfiguration("pysx").update("pythonPath", process.env.PYSX_EDITOR_PYTHON, vscode.ConfigurationTarget.Workspace);
  await vscode.workspace.getConfiguration("python").update("defaultInterpreterPath", process.env.PYSX_EDITOR_PYTHON, vscode.ConfigurationTarget.Workspace);
  const pylance = vscode.extensions.getExtension("ms-python.vscode-pylance");
  await pylance.activate();
  const extension = vscode.extensions.getExtension("pysx-local.pysx-lang");
  await extension.activate();
  const root = vscode.workspace.workspaceFolders[0].uri.fsPath;
  const filename = path.join(root, "lesson.py");
  fs.writeFileSync(filename, `from pysx import Fragment, pysx
from src.components.pages import Paragraphs
def app() -> Fragment:
    return pysx(t"Paragraphs(text={'Installed lesson'})")
`);
  const doc = await vscode.workspace.openTextDocument(filename);
  await vscode.window.showTextDocument(doc);
  await until(() => extension.exports.refresh(doc), "installed interpreter snapshot");
  const tag = doc.positionAt(doc.getText().indexOf("Paragraphs(text=") + 2);
  await until(async () => {
    const hover = await vscode.commands.executeCommand("vscode.executeHoverProvider", doc.uri, tag);
    return hover?.some(item => item.contents.some(part => String(part.value ?? part).includes("text: str")));
  }, "installed course component hover");
  await until(async () => {
    const definitions = await vscode.commands.executeCommand("vscode.executeDefinitionProvider", doc.uri, tag);
    return definitions?.some(item => {
      const filename = (item.uri ?? item.targetUri).fsPath;
      return filename.includes("site-packages") && filename.endsWith("src/components/pages.py");
    });
  }, "definition into installed course wheel");
  const replace = async (before, after) => {
    const index = doc.getText().indexOf(before);
    assert(index >= 0);
    const edit = new vscode.WorkspaceEdit();
    edit.replace(doc.uri, new vscode.Range(doc.positionAt(index), doc.positionAt(index + before.length)), after);
    assert(await vscode.workspace.applyEdit(edit));
    assert(await doc.save());
    await until(() => extension.exports.refresh(doc), "installed edit snapshot");
  };
  await replace("text={'Installed lesson'}", "texxt={'Installed lesson'}");
  await until(() => vscode.languages.getDiagnostics(doc.uri).some(item => item.source === "pysx" && item.message.includes("texxt")), "installed callable prop rejection");
  await replace("texxt={'Installed lesson'}", "text={'Corrected installed lesson'}");
  await until(() => !vscode.languages.getDiagnostics(doc.uri).some(item => item.source === "pysx" && item.message.includes("texxt")), "corrected installed callable");
  await vscode.languages.setTextDocumentLanguage(doc, "plaintext");
  await vscode.commands.executeCommand("workbench.action.closeAllEditors");
  await until(() => vscode.languages.getDiagnostics(doc.uri).every(item => item.source !== "pysx"), "installed course document teardown");
  fs.unlinkSync(filename);
  console.log(`VSCode ${vscode.version}; Pylance ${pylance.packageJSON.version}`);
  console.log("PYSX EDITOR CASES PASSED: 3 (installed course hover/definition, prop rejection/correction, document teardown)");
};
