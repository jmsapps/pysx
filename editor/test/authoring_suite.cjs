const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const vscode = require("vscode");

async function eventually(fn, label) {
  const end = Date.now() + 30000;
  while (Date.now() < end) {
    const value = await fn();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("timeout: " + label);
}
const position = (doc, text, delta = 0) => {
  const offset = doc.getText().indexOf(text);
  assert(offset >= 0, text);
  return doc.positionAt(offset + delta);
};
async function replace(doc, before, after) {
  const begin = doc.getText().indexOf(before); assert(begin >= 0);
  const edit = new vscode.WorkspaceEdit();
  edit.replace(doc.uri, new vscode.Range(doc.positionAt(begin), doc.positionAt(begin + before.length)), after);
  assert(await vscode.workspace.applyEdit(edit));
}

exports.run = async function () {
  const root = vscode.workspace.workspaceFolders[0].uri.fsPath;
  await vscode.extensions.getExtension("ms-python.python").activate();
  await vscode.workspace.getConfiguration("pysx").update("pythonPath", process.env.PYSX_EDITOR_PYTHON, vscode.ConfigurationTarget.Workspace);
  await vscode.workspace.getConfiguration("python").update("defaultInterpreterPath", process.env.PYSX_EDITOR_PYTHON, vscode.ConfigurationTarget.Workspace);
  await vscode.workspace.getConfiguration("python").update("analysis.typeCheckingMode", "strict", vscode.ConfigurationTarget.Workspace);
  fs.writeFileSync(path.join(root, "pyproject.toml"), '[tool.pyright]\ntypeCheckingMode = "strict"\n');
  fs.writeFileSync(path.join(root, "producer.py"), `from pysx import Children, Fragment, pysx
def Panel(*, title: str, maxWidth: int = 0, children: Children | None = None) -> Fragment:
    return pysx(t"section: {title}; {children}")
def UnusedWidget() -> Fragment:
    return pysx(t"p: 'unused'")
`);
  fs.mkdirSync(path.join(root, "pkg"));
  fs.writeFileSync(path.join(root, "pkg/__init__.py"), "");
  fs.writeFileSync(path.join(root, "pkg/exports.py"), "from producer import Panel as Panel\n");
  fs.writeFileSync(path.join(root, "closed.py"), `from pysx import Fragment, pysx
from producer import Panel
def app() -> Fragment:
    return pysx(t"br; Panel(title={'closed'})")
`);
  fs.writeFileSync(path.join(root, "aliases.py"), `from pysx import Fragment, pysx
from producer import Panel as lower
view = pysx(t"p: 'alias'; br; lower(title={'outer'})")
def shadow() -> Fragment:
    def lower(*, title: int) -> Fragment:
        return pysx(t"p: {title}")
    return pysx(t"br; lower(title={7})")
`);
  fs.writeFileSync(path.join(root, "consumer.py"), `from dataclasses import dataclass
from pysx import Fragment, each, pysx, signal
from producer import Panel, UnusedWidget
import pkg.exports
@dataclass
class Row:
    id: int
    title: str
rows = signal([Row(1, 'row')])
def app() -> Fragment:
    return pysx(t"""
br; Panel(title={'Hello'}): "😀 Panel quoted text"
input(type="text")
{each(rows, lambda row: pysx(t'br; Panel(title={row.title})'), key=lambda row: row.id)}
""")
ordinary = pkg.exports.Panel(title='ordinary')
def tree() -> Fragment:
    def branch(*, node: Row) -> Fragment:
        return pysx(t'br; Panel(title={node.title})')
    def abandoned() -> Fragment:
        return pysx(t'p: "unused"')
    return pysx(t'branch(node={rows()[0]}):')
`);
  const pylance = vscode.extensions.getExtension("ms-python.vscode-pylance");
  await pylance.activate();
  const producer = await vscode.workspace.openTextDocument(path.join(root, "producer.py"));
  const doc = await vscode.workspace.openTextDocument(path.join(root, "consumer.py"));
  await vscode.window.showTextDocument(doc);
  await eventually(async () => (await vscode.commands.executeCommand("vscode.executeHoverProvider", doc.uri, position(doc, "rows =")))?.length, "ordinary Python backend readiness");
  const extension = vscode.extensions.getExtension("pysx-local.pysx-lang");
  await extension.activate();
  const unmanaged = await vscode.workspace.openTextDocument({ language: "plaintext", content: "configuration guard" });
  await vscode.window.showTextDocument(unmanaged);
  const settingsBefore = JSON.stringify(vscode.workspace.getConfiguration("python", doc.uri).get("analysis.diagnosticSeverityOverrides", {}));
  assert.equal(await vscode.commands.executeCommand("pysx.configureTooling"), false);
  assert.equal(JSON.stringify(vscode.workspace.getConfiguration("python", doc.uri).get("analysis.diagnosticSeverityOverrides", {})), settingsBefore);
  await vscode.window.showTextDocument(doc);
  await eventually(async () => Boolean(await extension.exports.refresh(doc)), "saved inline-sibling snapshot");
  assert(!vscode.languages.getDiagnostics(doc.uri).some((item) => item.source === "pysx" && item.message.includes("F401") && item.message.includes("producer.Panel")), "saved later-sibling import retained");
  await replace(doc, "'Hello'", "'Unsaved'");
  await eventually(async () => Boolean(await extension.exports.refresh(doc)), "immutable snapshot");
  await eventually(() => vscode.languages.getDiagnostics(doc.uri).some((item) => item.source === "pysx" && item.message.includes("F401") && item.message.includes("UnusedWidget")), "genuine unused import");
  assert(!vscode.languages.getDiagnostics(doc.uri).some((item) => item.source === "pysx" && item.message.includes("F401") && item.message.includes("producer.Panel")));
  assert(vscode.extensions.getExtension("charliermarsh.ruff"), "actual Ruff extension must exercise its nullable configuration");
  await vscode.workspace.getConfiguration("ruff", doc.uri).update("lint.ignore", null, vscode.ConfigurationTarget.WorkspaceFolder);
  assert.equal(vscode.workspace.getConfiguration("ruff", doc.uri).get("lint.ignore"), null);
  assert.equal(await vscode.commands.executeCommand("pysx.configureTooling"), true);
  const configuredProject = fs.readFileSync(path.join(root, "pyproject.toml"), "utf8");
  assert(configuredProject.includes("reportUnusedFunction = false"));
  assert(configuredProject.includes('typeCheckingMode = "strict"'));
  await eventually(async () => Boolean(await extension.exports.refresh(doc)), "snapshot after effective configuration change");
  const rule = (item) => String(item.code?.value ?? item.code);
  await eventually(() => !vscode.languages.getDiagnostics(doc.uri).some((item) => item.source !== "pysx" && ["reportUnusedImport", "reportUnusedFunction"].includes(rule(item))), "Pylance effective diagnostic handoff");
  await eventually(() => vscode.languages.getDiagnostics(doc.uri).some((item) => item.source === "pysx" && rule(item) === "reportUnusedFunction" && item.message.includes("abandoned")), "genuine unused function retained");
  assert(!vscode.languages.getDiagnostics(doc.uri).some((item) => rule(item) === "reportUnusedFunction" && item.message.includes('"branch"')));
  assert(vscode.workspace.getConfiguration("ruff", doc.uri).get("lint.ignore").includes("F401"));
  await vscode.workspace.getConfiguration("ruff", doc.uri).update("lint.ignore", ["E501"], vscode.ConfigurationTarget.WorkspaceFolder);
  assert.equal(await vscode.commands.executeCommand("pysx.configureTooling"), true);
  assert(vscode.workspace.getConfiguration("ruff", doc.uri).get("lint.ignore").includes("E501"));
  assert.equal(vscode.workspace.getConfiguration("python", doc.uri).get("analysis.typeCheckingMode"), "strict");

  const tag = position(doc, "Panel(title=", 2);
  const hover = await eventually(async () => {
    const values = await vscode.commands.executeCommand("vscode.executeHoverProvider", doc.uri, tag);
    return values?.some((item) => item.contents.some((part) => String(part.value ?? part).includes("title: str"))) ? values : null;
  }, "tag signature hover");
  assert(hover.length);
  const completion = await eventually(async () => {
    const values = await vscode.commands.executeCommand("vscode.executeCompletionItemProvider", doc.uri, tag);
    return values?.items.some((item) => String(item.label.label ?? item.label) === "Panel") ? values : null;
  }, "tag completion");
  assert(completion.items.length);
  const definition = await eventually(async () => {
    const values = await vscode.commands.executeCommand("vscode.executeDefinitionProvider", doc.uri, tag);
    return values?.some((item) => (item.uri ?? item.targetUri).fsPath === producer.fileName) ? values : null;
  }, "original definition");
  assert(!definition.some((item) => (item.uri ?? item.targetUri).fsPath.includes("_pysx_revision_")));
  const references = await vscode.commands.executeCommand("vscode.executeReferenceProvider", doc.uri, tag);
  assert(references.some((item) => item.uri.fsPath === doc.fileName && doc.getText(item.range) === "Panel"));
  assert(references.every((item) => !item.uri.fsPath.includes("_pysx_revision_")));
  const props = await eventually(async () => {
    const values = await vscode.commands.executeCommand("vscode.executeCompletionItemProvider", doc.uri, position(doc, "title={'Unsaved'}", 3));
    return values?.items.some((item) => String(item.label.label ?? item.label) === "title") ? values : null;
  }, "callable prop completion");
  assert(props.items.length);
  assert(props.items.some((item) => String(item.label.label ?? item.label) === "maxWidth"));
  assert(!props.items.some((item) => String(item.label.label ?? item.label) === "maxwidth"));
  const nativeProps = await vscode.commands.executeCommand("vscode.executeCompletionItemProvider", doc.uri, position(doc, 'type="text"', 2));
  assert(nativeProps.items.some((item) => String(item.label.label ?? item.label) === "type"));

  const aliasDoc = await vscode.workspace.openTextDocument(path.join(root, "aliases.py"));
  await vscode.window.showTextDocument(aliasDoc);
  await eventually(async () => Boolean(await extension.exports.refresh(aliasDoc)), "saved lowercase alias snapshot");
  assert(!vscode.languages.getDiagnostics(aliasDoc.uri).some((item) => item.source === "pysx" && item.message.includes("F401") && item.message.includes("lower")), "saved lowercase tag import retained");
  const aliasTag = position(aliasDoc, "lower(title={'outer'})", 2);
  const aliasDefinitions = await eventually(async () => {
    const values = await vscode.commands.executeCommand("vscode.executeDefinitionProvider", aliasDoc.uri, aliasTag);
    return values?.some(item => (item.uri ?? item.targetUri).fsPath === producer.fileName) ? values : null;
  }, "third-sibling lowercase alias definition");
  assert(aliasDefinitions.length);
  const localTag = position(aliasDoc, "lower(title={7})", 2);
  const localDefinitions = await eventually(async () => {
    const values = await vscode.commands.executeCommand("vscode.executeDefinitionProvider", aliasDoc.uri, localTag);
    return values?.some(item => (item.uri ?? item.targetUri).fsPath === aliasDoc.fileName) ? values : null;
  }, "second-sibling shadowed local definition");
  assert(localDefinitions.every(item => (item.uri ?? item.targetUri).fsPath !== producer.fileName));
  await replace(aliasDoc, "'alias'", "'unsaved alias'");
  await eventually(async () => Boolean(await extension.exports.refresh(aliasDoc)), "unsaved lowercase alias snapshot");
  const aliasReferences = await eventually(async () => {
    const values = await vscode.commands.executeCommand("vscode.executeReferenceProvider", aliasDoc.uri, position(aliasDoc, "lower(title={'outer'})", 2));
    return values?.some(item => item.uri.fsPath === aliasDoc.fileName && aliasDoc.getText(item.range) === "lower") ? values : null;
  }, "unsaved third-sibling alias references");
  assert(aliasReferences.some(item => item.uri.fsPath === aliasDoc.fileName && aliasDoc.getText(item.range) === "lower"));
  assert(!vscode.languages.getDiagnostics(aliasDoc.uri).some((item) => item.source === "pysx" && item.message.includes("F401") && item.message.includes("lower")), "unsaved lowercase tag import retained");
  await vscode.window.showTextDocument(doc);

  const actions = await vscode.commands.executeCommand("vscode.executeCodeActionProvider", doc.uri, new vscode.Range(0, 0, 0, 0), "source.organizeImports.pysx");
  const action = actions.find((item) => item.command?.command === "pysx.applyImports"); assert(action);
  await replace(doc, "'Unsaved'", "'Newest'");
  assert.equal(await vscode.commands.executeCommand(action.command.command, ...action.command.arguments), false, "stale action rejected");
  await eventually(async () => Boolean(await extension.exports.refresh(doc)), "fresh snapshot after edit");
  const fresh = (await vscode.commands.executeCommand("vscode.executeCodeActionProvider", doc.uri, new vscode.Range(0, 0, 0, 0), "source.organizeImports.pysx")).find((item) => item.command?.command === "pysx.applyImports");
  assert.equal(await vscode.commands.executeCommand(fresh.command.command, ...fresh.command.arguments), true);
  assert(!doc.getText().includes("UnusedWidget")); assert(doc.getText().includes("from producer import Panel"));
  await replace(doc, "from producer import Panel", "from producer import Panel\nimport statistics");
  await eventually(async () => Boolean(await extension.exports.refresh(doc)), "save action snapshot");
  await doc.save();
  assert(!doc.getText().includes("import statistics"), "owned save action removes real unused import");
  assert(doc.getText().includes("from producer import Panel"), "owned save action preserves tag import");

  await replace(doc, "title={'Newest'}", "title={42}");
  await replace(doc, "ordinary =", "ordinary_bad: str = 42\nordinary =");
  await eventually(() => vscode.languages.getDiagnostics(doc.uri).some((item) => String(item.code?.value ?? item.code) === "reportAssignmentType"), "ordinary strict Python diagnostics");
  await eventually(() => vscode.languages.getDiagnostics(doc.uri).some((item) => item.source === "pysx" && item.message.includes('str') && doc.getText(item.range) === "42"), "unsaved prop type diagnostic").catch((error) => {
    console.log(JSON.stringify(vscode.workspace.textDocuments.filter((item) => item.fileName.endsWith("consumer.py")).map((item) => ({ file: item.fileName, language:item.languageId, mode:vscode.workspace.getConfiguration("python",item.uri).get("analysis.typeCheckingMode"), text: item.getText(), diagnostics: vscode.languages.getDiagnostics(item.uri).map((d) => ({code:d.code, message:d.message, range:d.range})) }))));
    const logs = path.join(process.env.PYSX_EDITOR_WORKSPACE, "profile/logs");
    for (const name of fs.readdirSync(logs, {recursive:true}).filter((item) => item.includes("pylance") && item.endsWith(".log"))) {
      console.log(name, fs.readFileSync(path.join(logs,name), "utf8").slice(-18000));
    }
    throw error;
  });
  assert(vscode.workspace.textDocuments.filter((item) => item.fileName.includes("_pysx_revision_")).every((item) => !vscode.languages.getDiagnostics(item.uri).length), "private diagnostics are not published");
  await replace(doc, "title={42}", "title={'Correct'}");
  await replace(doc, "ordinary_bad: str = 42\n", "");
  await eventually(async () => Boolean(await extension.exports.refresh(doc)), "corrected snapshot");
  await eventually(() => !vscode.languages.getDiagnostics(doc.uri).some((item) => item.source === "pysx" && item.message.includes("int")), "stale type diagnostics removed");
  await replace(producer, "title: str", "title: int");
  await eventually(() => vscode.languages.getDiagnostics(doc.uri).some((item) => item.source === "pysx" && item.message.includes('type "int"')), "unsaved dependent signature");
  await replace(producer, "title: int", "title: str");
  await eventually(async () => Boolean(await extension.exports.refresh(doc)), "restored dependent signature");
  await eventually(() => !vscode.languages.getDiagnostics(doc.uri).some((item) => item.source === "pysx" && item.message.includes('type "int"')), "dependent recovery");
  await replace(doc, "title={'Correct'}", "title=");
  await eventually(async () => Boolean(await extension.exports.refresh(doc)), "incomplete snapshot");
  const incompleteActions = await vscode.commands.executeCommand("vscode.executeCodeActionProvider", doc.uri, new vscode.Range(0,0,0,0), "source.organizeImports.pysx");
  assert(!incompleteActions.some((item) => item.command?.command === "pysx.applyImports"));
  await replace(doc, "title=)", "title={'Correct'})");
  await replace(doc, "'Correct'", "'rapid one'");
  const obsolete = extension.exports.refresh(doc);
  await replace(doc, "'rapid one'", "'Correct'");
  await eventually(async () => Boolean(await extension.exports.refresh(doc)), "rapid-edit recovery");
  await obsolete;

  await vscode.window.showTextDocument(producer);
  const start = position(producer, "def Panel", 5);
  vscode.window.activeTextEditor.selection = new vscode.Selection(start, start);
  assert.equal(await vscode.commands.executeCommand("pysx.rename", "CardPanel"), true, "coordinated rename");
  assert(producer.getText().includes("def CardPanel"));
  assert(doc.getText().includes("CardPanel(title="));
  assert(doc.getText().includes("😀 Panel quoted text"));
  const closed = await vscode.workspace.openTextDocument(path.join(root, "closed.py"));
  assert(closed.getText().includes("CardPanel(title="));
  assert(aliasDoc.getText().includes("from producer import CardPanel as lower"));
  assert(aliasDoc.getText().includes("def lower(*, title: int)"));
  assert(aliasDoc.getText().includes("lower(title={7})"));
  if (process.env.PYSX_EDITOR_PERFORMANCE === "1") {
    const checkout = process.env.PYSX_EDITOR_CHECKOUT;
    const result = spawnSync(process.env.PYSX_EDITOR_PYTHON, ["-c",
      "import json,sys; from pathlib import Path; from pysx.quality import source_files; root=Path(sys.argv[1]); print(json.dumps([str(path.relative_to(root)) for path in source_files(root)]))", checkout],
      { encoding: "utf8", timeout: 30000 });
    assert.equal(result.status, 0, result.stderr);
    const files = JSON.parse(result.stdout);
    for (const relative of files) {
      const destination = path.join(root, relative);
      fs.mkdirSync(path.dirname(destination), { recursive: true });
      fs.copyFileSync(path.join(checkout, relative), destination);
    }
    fs.copyFileSync(path.join(checkout, "pyproject.toml"), path.join(root, "pyproject.toml"));
    const filename = path.join(root, "production_probe.py");
    fs.writeFileSync(filename, "from pysx import pysx\nfrom examples.components.controls import Action\nview = pysx(t\"Action(id={'performance'}): 'Run'\")\n");
    const large = await vscode.workspace.openTextDocument(filename);
    await vscode.window.showTextDocument(large);
    await new Promise((resolve) => setTimeout(resolve, 500));
    const coldStart = Date.now();
    const cold = await extension.exports.refresh(large);
    assert(cold?.models.length >= files.length, "whole maintained graph analyzed");
    const coldMs = Date.now() - coldStart;
    const warmStart = Date.now();
    await replace(large, "'performance'", "'edited performance'");
    const warm = await extension.exports.refresh(large);
    assert(warm?.models.length >= files.length);
    const warmMs = Date.now() - warmStart;
    const hoverStart = Date.now();
    const hovers = await vscode.commands.executeCommand("vscode.executeHoverProvider", large.uri, position(large, "Action(id", 2));
    assert(hovers?.length, "real Pylance hover after whole-graph edit");
    assert(!vscode.languages.getDiagnostics(large.uri).some((item) => item.source === "pysx" && item.message.includes("F401") && item.message.includes("Action")));
    console.log("PYSX EDITOR PERFORMANCE: " + JSON.stringify({ files: warm.models.length, cold_snapshot_ms: coldMs, edited_snapshot_ms: warmMs, ready_hover_ms: Date.now() - hoverStart }));
  }
  console.log("PYSX EDITOR CASES PASSED: 10 (live buffers, imports, hover, completion, definition, props, stale fixes, cleanup, type recovery, coherent rename)");
};
