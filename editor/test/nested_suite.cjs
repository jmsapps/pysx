const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vscode = require("vscode");

const APPLICATION = process.env.PYSX_EDITOR_APPLICATION;
const REPOSITORY = process.env.PYSX_EDITOR_REPOSITORY;
const VIEW = path.join(APPLICATION, "app", "containers", "tutorial", "view.py");
const CONTROLS = path.join(APPLICATION, "app", "components", "controls", "__init__.py");
const SHADOW = path.join(REPOSITORY, "app", "components", "controls", "__init__.py");

async function until(predicate, label, timeout = 90000) {
  const started = Date.now();
  let last;
  while (Date.now() < started + timeout) {
    last = await predicate();
    if (last) return last;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`timeout waiting for ${typeof label === "function" ? label() : label} after ${((Date.now() - started) / 1000).toFixed(1)}s, last: ${JSON.stringify(last)}`);
}

function sitePosition(document, needle, after) {
  const text = document.getText();
  const start = text.indexOf(needle, after === undefined ? 0 : text.indexOf(after) + after.length);
  assert(start >= 0, `source contains ${needle}`);
  return document.positionAt(start + 1);
}

// A definition is only correct when it names the user's own source and the symbol's
// own range; equality between two wrong destinations is not a pass.
async function definitionOf(uri, position, label) {
  return until(async () => {
    const found = await vscode.commands.executeCommand("vscode.executeDefinitionProvider", uri, position);
    return found && found.length ? found : null;
  }, `a definition for ${label}`);
}

function destination(found) {
  const first = found[0];
  const uri = first.uri ?? first.targetUri;
  const range = first.range ?? first.targetSelectionRange ?? first.targetRange;
  return { file: fs.realpathSync.native(uri.fsPath), line: range.start.line, character: range.start.character };
}

async function open(file) {
  const document = await vscode.workspace.openTextDocument(file);
  await vscode.window.showTextDocument(document);
  return document;
}

exports.run = async function (mode, progress) {
  const passed = [];
  const controls = await vscode.workspace.openTextDocument(CONTROLS);
  const buttonDefinition = sitePosition(controls, "Button = styled");
  const captionDefinition = sitePosition(controls, "Caption() ->");

  progress("open the nested application document from the repository root");
  const document = await open(VIEW);
  assert.equal(vscode.workspace.getWorkspaceFolder(document.uri).name, "repo",
    "the containing workspace folder is the outer repository root, not the application");

  // 1/2. The reported failing case and its ordinary-component control, each asserted
  // independently against the real user source.
  progress("resolve the styled tag from the repository root");
  const styled = destination(await definitionOf(document.uri,
    sitePosition(document, "Button(id=", "pysx(t"), "the styled tag"));
  assert.equal(styled.file, fs.realpathSync.native(CONTROLS), "the styled tag resolves into the application");
  assert.equal(styled.line, buttonDefinition.line, "the styled binding's own line");
  passed.push("styled tag definition");

  progress("resolve the ordinary component from the repository root");
  const ordinary = destination(await definitionOf(document.uri,
    sitePosition(document, "Caption;", "pysx(t"), "the ordinary component tag"));
  assert.equal(ordinary.file, fs.realpathSync.native(CONTROLS));
  assert.equal(ordinary.line, captionDefinition.line, "the component function's own line");
  passed.push("ordinary component definition");

  // 3. A native tag is a control: it is served by the schema, never by user source.
  progress("verify the native tag resolves to no application source");
  const native = await vscode.commands.executeCommand("vscode.executeDefinitionProvider",
    document.uri, sitePosition(document, "br'", "Caption"));
  const nativeFiles = (native ?? []).map((item) => (item.uri ?? item.targetUri).fsPath);
  assert(!nativeFiles.includes(CONTROLS), `a native tag never resolves into application source: ${nativeFiles}`);
  passed.push("native tag control");

  // 4. The same-named package nearer the outer root may not win.
  progress("reject the shadowing package nearer the repository root");
  assert(fs.existsSync(SHADOW), "the shadowing package exists");
  assert.notEqual(styled.file, fs.realpathSync.native(SHADOW), "the outer same-named package never shadows");
  passed.push("shadowing package rejected");

  // 5. Opening the application directly must produce the identical destination.
  progress("add the application as its own workspace folder");
  const before = vscode.workspace.workspaceFolders.length;
  assert(vscode.workspace.updateWorkspaceFolders(before, 0,
    { uri: vscode.Uri.file(APPLICATION), name: "application" }), "the application folder is added");
  await until(async () => vscode.workspace.workspaceFolders.length === before + 1, "the added folder");
  await vscode.commands.executeCommand("workbench.action.closeAllEditors");
  const reopened = await open(VIEW);
  if (mode !== "worker") {
    const api = vscode.extensions.getExtension("jmsapps.pysx").exports;
    await until(async () => {
      const owner = api.owner(reopened.uri);
      return owner?.lifecycle?.status().running ? owner : null;
    }, () => `a running client for the application folder: ${JSON.stringify({
      owner: api.owner(reopened.uri)?.folder?.name,
      error: api.owner(reopened.uri)?.error,
      workspaces: api.workspaces(),
    })}`);
  }
  assert.equal(vscode.workspace.getWorkspaceFolder(reopened.uri).name, "application",
    "the application now owns its own document");
  const direct = destination(await definitionOf(reopened.uri,
    sitePosition(reopened, "Button(id=", "pysx(t"), "the styled tag at the application root"));
  assert.deepEqual(direct, styled, "both workspace layouts reach the same user source and range");
  passed.push("repository-root and application-root equivalence");

  // 6. An unsaved edit resolves without writing the file.
  progress("resolve a tag introduced by an unsaved edit");
  const editor = vscode.window.activeTextEditor;
  const anchor = reopened.getText().indexOf("; br");
  await editor.edit((builder) => builder.insert(reopened.positionAt(anchor), "; Caption"));
  assert(reopened.isDirty, "the edit is unsaved");
  const unsaved = destination(await definitionOf(reopened.uri,
    sitePosition(reopened, "Caption;", "pysx(t"), "the unsaved tag"));
  assert.equal(unsaved.file, fs.realpathSync.native(CONTROLS), "unsaved edits resolve into the application");
  assert.equal(unsaved.line, captionDefinition.line);
  passed.push("unsaved edit resolution");

  if (mode === "worker") {
    // 7. The worker builds its projection mirror inside the application's own project,
    // which is the observable form of per-project routing in this adapter.
    progress("verify the worker analyses the application project");
    const mirrored = (directory) => fs.existsSync(directory) &&
      fs.readdirSync(directory).some((entry) => entry.startsWith("_pysx_revision_"));
    await until(async () => mirrored(APPLICATION), "the application's own projection mirror");
    assert(!mirrored(REPOSITORY), "the repository root is never the worker's analysis root");
    const own = path.join(APPLICATION, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
    assert(fs.existsSync(own), "the application owns an environment the sibling folders do not");
    passed.push("per-project worker analysis root");
  } else {
    // 7. Analysis is partitioned per project, so the server analyses the application.
    progress("verify the analysed root is the application project");
    const api = vscode.extensions.getExtension("jmsapps.pysx").exports;
    const owner = api.owner(reopened.uri);
    const status = await until(async () => {
      if (!owner?.lifecycle?.status().running) return null;
      try { return await owner.lifecycle.client.sendRequest("pysx/status", {}); }
      catch { return null; }
    }, "a status response for the application");
    const analysed = status.workspaces.map((item) => fs.realpathSync.native(item.root));
    assert(analysed.includes(fs.realpathSync.native(APPLICATION)),
      `the application is analysed as its own project: ${JSON.stringify(analysed)}`);
    assert(!analysed.includes(fs.realpathSync.native(REPOSITORY)), "the repository root is not an analysis root");
    passed.push("per-project analysis root");

    // 8. The unrelated outer sources never consume the application's limits.
    progress("verify the large outer repository is excluded");
    const outer = fs.readdirSync(path.join(REPOSITORY, "unrelated")).length;
    assert(outer > 512, "the outer repository exceeds a single workspace's source limit");
    assert.equal(owner.error, null, "the application analyses despite the large outer repository");
    passed.push("per-project source bounds");
  }
  await vscode.commands.executeCommand("workbench.action.closeAllEditors");

  return passed;
};
