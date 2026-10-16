const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vscode = require("vscode");

async function until(predicate, label, timeout = 60000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    last = await predicate();
    if (last) return last;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`timeout waiting for ${label}`);
}

async function pythonSemantics(document, expected) {
  return until(async () => {
    const legend = await vscode.commands.executeCommand(
      "vscode.provideDocumentSemanticTokensLegend", document.uri);
    const encoded = await vscode.commands.executeCommand(
      "vscode.provideDocumentSemanticTokens", document.uri);
    if (!legend || !encoded?.data?.length) return null;
    const tokens = [];
    let line = 0, character = 0;
    for (let index = 0; index < encoded.data.length; index += 5) {
      const [deltaLine, deltaCharacter, length, type] = encoded.data.slice(index, index + 5);
      line += deltaLine;
      character = deltaLine ? deltaCharacter : character + deltaCharacter;
      const range = new vscode.Range(line, character, line, character + length);
      tokens.push({ text: document.getText(range), type: legend.tokenTypes[type], range });
    }
    return expected.every(([text, type, marker]) => tokens.some((token) =>
      token.text === text && token.type === type && (!marker ||
        document.lineAt(token.range.start.line).text.includes(marker)))) ? tokens : null;
  }, `Pylance Python semantic tokens: ${expected.map(([text]) => text).join(", ")}`);
}

exports.pythonSemantics = pythonSemantics;

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
  "def Panel(*, title: str) -> Fragment:",
  "    return pysx(t'p: {title}')",
  "",
  "",
  "def view() -> Fragment:",
  "    return pysx(",
  '        t"""',
  "        div(class=\"wrap\", css=\"color: rebeccapurple;\"):",
  "              Panel(title=\"hello\")",
  '    """,',
  "    )",
  "",
  "",
  "renderer = pysx",
  "",
  "",
  "def shared_view(label: str) -> Fragment:",
  "    shared = t'\"🌍\"; span(title=\"shared\", css=\"color: rebeccapurple;\"): {label}'",
  "    first = renderer(shared)",
  "    return pysx(shared)",
  "",
  "",
  "def aliased_view(label: str) -> Fragment:",
  "    return renderer(t'section(id=\"alias\"): {label}')",
  "",
  'AFTER_SENTINEL = "plain python again"',
].join("\n");

exports.run = async function () {
  const unhandled = [];
  const onUnhandled = (error) => unhandled.push(String(error?.stack ?? error));
  process.on("unhandledRejection", onUnhandled);
  const extension = vscode.extensions.getExtension("pysx-local.pysx-lang");
  assert(extension, "fresh extension registered");
  const python = process.env.PYSX_EDITOR_PYTHON;
  assert(python, "selected interpreter supplied to the host");

  // The bundled client is opt-in; the worker integration stays the default.
  const settings = vscode.workspace.getConfiguration("pysx");
  assert.equal(settings.inspect("useLanguageServer").defaultValue, false, "worker remains default");
  await settings.update("pythonPath", python, vscode.ConfigurationTarget.Workspace);
  await settings.update("useLanguageServer", true, vscode.ConfigurationTarget.Workspace);

  const pylance = vscode.extensions.getExtension("ms-python.vscode-pylance");
  assert(pylance, "actual Pylance registered");
  const activationOrder = process.env.PYSX_EDITOR_ACTIVATION_ORDER ?? "client-first";
  assert(["client-first", "pylance-first"].includes(activationOrder), "known activation order");
  if (activationOrder === "pylance-first") await pylance.activate();

  const api = await extension.activate();
  assert(extension.isActive, "actual extension activation");
  assert(api && api.client, "resident client activated instead of the worker");
  assert.equal(api.interpreter, python, "client launched the selected interpreter");

  // The bundled client must come from the installed VSIX, not a checkout.
  const bundled = path.join(extension.extensionPath, "node_modules", "vscode-languageclient");
  assert(fs.existsSync(bundled), "vscode-languageclient bundled in the installed extension");
  const checkout = (process.env.PYSX_EDITOR_CHECKOUT ?? "").replace(/[\\/]+$/, "");
  assert(checkout && path.isAbsolute(checkout), "checkout root supplied to the host");
  assert(!extension.extensionPath.startsWith(checkout + path.sep), "installed outside the checkout");

  if (activationOrder === "client-first") await pylance.activate();
  assert.equal(api.client.initializeResult.capabilities.semanticTokensProvider, undefined,
    "resident client does not register a competing Python semantic-token provider");
  assert(api.highlighting, "resident markup decoration controller is available");
  console.log(`VSCode ${vscode.version}; Pylance ${pylance.packageJSON.version}; client ${api.client.name}; ${activationOrder}`);

  const filename = path.join(vscode.workspace.workspaceFolders[0].uri.fsPath, "client_verify.py");
  fs.writeFileSync(filename, SOURCE);
  const document = await vscode.workspace.openTextDocument(filename);
  await vscode.languages.setTextDocumentLanguage(document, "python");
  await vscode.window.showTextDocument(document);

  // A legend alone cannot show which provider won. Decode actual Python tokens
  // while markup and interpolation expressions exist in the same document.
  await pythonSemantics(document, [["view", "function"], ["label", "parameter", "{label}"]]);
  const markup = await until(async () => {
    await api.highlighting.refresh(document);
    const applied = api.highlighting.applied(document.uri);
    return applied?.version === document.version && applied.tokens.length ? applied : null;
  }, "current nonliteral markup decorations");
  const decoratedText = (token) => document.getText(new vscode.Range(
    token.range.start.line, token.range.start.character,
    token.range.end.line, token.range.end.character));
  for (const name of ["span", "title", "section", "id"]) {
    assert(markup.tokens.some((token) => decoratedText(token) === name),
      `statically resolved shared/aliased markup ${name} is decorated`);
  }
  assert(!markup.tokens.some((token) => ["div", "Panel", "label", "AFTER_SENTINEL"]
    .includes(decoratedText(token))), "decorations avoid direct literals and Python expressions");
  const cssOffset = SOURCE.indexOf("color: rebeccapurple;", SOURCE.indexOf("shared = t"));
  const cssEnd = cssOffset + "color: rebeccapurple;".length;
  const offsetAt = (position) => document.offsetAt(
    new vscode.Position(position.line, position.character));
  assert(!markup.tokens.some((token) =>
    offsetAt(token.range.start) < cssEnd &&
    offsetAt(token.range.end) > cssOffset), "markup decorations do not overlay CSS values");
  assert(Number.isInteger(markup.generation), "decoration metadata carries its analysis generation");

  const symbols = await until(async () => {
    const found = await vscode.commands.executeCommand(
      "vscode.executeDocumentSymbolProvider", document.uri);
    return found && found.some((item) => item.name === "div") ? found : null;
  }, "markup document symbols");
  assert(symbols.some((item) => item.name === "div"), "markup symbol from the server");

  const edits = await until(async () => {
    const found = await vscode.commands.executeCommand(
      "vscode.executeFormatDocumentProvider", document.uri,
      { tabSize: 4, insertSpaces: true });
    return found && found.length ? found : null;
  }, "template formatting edits");
  assert(edits.length, "formatter normalized the ragged template");

  // An unsaved mistake must surface without writing the file.
  const edit = new vscode.WorkspaceEdit();
  const line = SOURCE.split("\n").findIndex((text) => text.includes("Panel(title="));
  edit.replace(document.uri, new vscode.Range(line, 14, line, 19), "Pnel");
  assert(await vscode.workspace.applyEdit(edit));
  assert(document.isDirty, "mistake stays unsaved");
  const diagnostic = await until(() => vscode.languages.getDiagnostics(document.uri)
    .find((item) => item.source === "pysx" && item.message.includes("Pnel")),
    "unsaved-buffer diagnostic");
  assert.equal(diagnostic.range.start.line, line);

  // Two unsaved changes may supersede in-flight requests. Only the current
  // document version may remain applied, including UTF-16 offsets after an emoji.
  const sharedLine = document.getText().split("\n").findIndex((text) => text.includes("shared = t"));
  const pendingHighlights = [];
  for (const replacement of ["strong", "article"]) {
    const current = document.lineAt(sharedLine).text;
    const tag = current.match(/; (span|strong)\(/)[1];
    const column = current.indexOf(`; ${tag}(`) + 2;
    const change = new vscode.WorkspaceEdit();
    change.replace(document.uri,
      new vscode.Range(sharedLine, column, sharedLine, column + tag.length), replacement);
    assert(await vscode.workspace.applyEdit(change));
    pendingHighlights.push(api.highlighting.refresh(document).then(
      () => null, (error) => error));
  }
  for (const error of await Promise.all(pendingHighlights)) assert.equal(error, null);
  await until(async () => {
    await api.highlighting.refresh(document);
    const applied = api.highlighting.applied(document.uri);
    return applied?.version === document.version && applied.tokens.some((token) =>
      decoratedText(token) === "article");
  }, "latest unsaved markup version after rapid edits");
  assert(!api.highlighting.applied(document.uri).tokens.some((token) =>
    ["span", "strong"].includes(decoratedText(token))), "superseded markup ranges are discarded");
  await pythonSemantics(document, [["shared_view", "function"], ["label", "parameter", "{label}"]]);

  // Bounded restart leaves exactly one running server and no duplicate providers.
  assert(api.lifecycle, "resident client exposes its lifecycle owner");
  const ownedPids = new Set();
  async function owned() {
    const state = await until(async () => {
      if (!api.lifecycle.status().running) return null;
      try {
        const found = await api.client.sendRequest("pysx/status", {});
        return found.processId && found.workspaces.length === 1 &&
          found.workspaces[0].ready && found.workspaces[0].pid ? found : null;
      } catch { return null; }
    }, "one ready server/backend ownership chain");
    ownedPids.add(state.processId);
    ownedPids.add(state.workspaces[0].pid);
    return state;
  }
  async function gone(state) {
    await until(() => !alive(state.processId) && !alive(state.workspaces[0].pid),
      "the prior server and backend both exit", 10000);
  }
  const initial = await owned();
  await vscode.commands.executeCommand("pysx.restartServer");
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.deepEqual(unhandled, [], "intentional restart produces no unhandled rejection");
  await gone(initial);
  await until(async () => {
    const found = await vscode.commands.executeCommand(
      "vscode.executeDocumentSymbolProvider", document.uri);
    return found && found.some((item) => item.name === "div");
  }, "features recover after restart");

  const concurrent = await owned();
  const before = api.lifecycle.status();
  await Promise.all(Array.from({ length: 3 }, () =>
    vscode.commands.executeCommand("pysx.restartServer")));
  await gone(concurrent);
  await owned();
  assert.equal(api.lifecycle.status().starts, before.starts + 1,
    "overlapping manual requests share one restart");
  assert.equal(api.lifecycle.status().restarts, 0, "manual restart resets the crash budget once");

  // Actual server death must recover exactly four times, then stop. Every
  // superseded backend must follow its real parent process out of the system.
  for (let crash = 0; crash < 5; crash += 1) {
    const prior = await owned();
    const starts = api.lifecycle.status().starts;
    process.kill(prior.processId, "SIGKILL");
    if (crash < 4) {
      await until(() => api.lifecycle.status().starts === starts + 1,
        `automatic crash recovery ${crash + 1}`);
      const recovered = await owned();
      assert.notEqual(recovered.processId, prior.processId);
      assert.equal(api.lifecycle.status().restarts, crash + 1);
    } else {
      await until(() => api.lifecycle.status().exhausted && !api.lifecycle.status().running,
        "automatic restart budget is exhausted");
      assert.equal(api.lifecycle.status().starts, starts);
      assert.equal(api.lifecycle.status().restarts, 4);
    }
    await gone(prior);
  }
  await vscode.commands.executeCommand("pysx.restartServer");
  const recovered = await owned();
  assert.equal(api.lifecycle.status().restarts, 0, "manual recovery resets an exhausted budget");
  const markupProviders = await vscode.commands.executeCommand(
    "vscode.executeDocumentSymbolProvider", document.uri);
  assert.equal(markupProviders.filter((symbol) => symbol.name === "div").length, 1,
    "crash recovery registers the markup provider exactly once");

  const starts = api.lifecycle.status().starts;
  await api.lifecycle.stop();
  await gone(recovered);
  assert.equal(api.lifecycle.status().starts, starts, "intentional stop does not recover itself");
  assert(!api.client.isRunning());
  assert(!api.highlighting.applied(document.uri), "intentional stop clears markup decorations");
  await api.lifecycle.start();
  await owned();

  const startupOutput = vscode.window.createOutputChannel("pysx lifecycle startup test", { log: true });
  const failed = require(path.join(extension.extensionPath, "client.js")).createClient({
    python, output: startupOutput,
    serverOptions: { command: path.join(path.dirname(filename), "missing-interpreter"), args: [] },
  });
  try {
    await assert.rejects(failed.start(), "startup failure is a handled rejected operation");
    assert.equal(failed.status().running, false);
    assert.equal(failed.status().restarts, 0, "failed startup does not enter a restart loop");
  } finally {
    await failed.dispose();
    startupOutput.dispose();
  }

  await require(path.join(extension.extensionPath, "extension.js")).deactivate();
  assert.equal(api.lifecycle.status().disposed, true, "real exported deactivation is awaited");
  assert.equal(api.lifecycle.status().running, false);
  await until(() => [...ownedPids].every((pid) => !alive(pid)), "deactivation leaves no owned process");
  const disposedStarts = api.lifecycle.status().starts;
  await api.lifecycle.restart();
  assert.equal(api.lifecycle.status().starts, disposedStarts, "disposed lifecycle cannot restart");
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.deepEqual(unhandled, [], "lifecycle operations produce no unhandled rejection");

  await vscode.languages.setTextDocumentLanguage(document, "plaintext");
  await until(() => !api.highlighting.applied(document.uri), "decoration teardown on language change");
  await vscode.commands.executeCommand("workbench.action.closeAllEditors");
  await until(() => vscode.languages.getDiagnostics(document.uri)
    .every((item) => item.source !== "pysx"), "diagnostic teardown");
  fs.unlinkSync(filename);
  process.removeListener("unhandledRejection", onUnhandled);
  console.log(
    "PYSX EDITOR CASES PASSED: 12 (bundled activation, interpreter selection, " +
    "Pylance coexistence, markup, symbols, formatting, unsaved edits, serialized restart, " +
    "crash budget, recovery, startup failure and process teardown)");
};
