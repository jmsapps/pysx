const vscode = require("vscode");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFile } = require("node:child_process");

const selector = { language: "python", scheme: "file" };
const canonical = (file) => fs.existsSync(file) ? fs.realpathSync(file) : path.resolve(file);
const generated = (file) => file.split(path.sep).some((part) => part.startsWith("_pysx_revision_"));
const managed = (doc) => doc?.languageId === "python" && doc.uri.scheme === "file" && !generated(doc.fileName);
const digest = (text) => crypto.createHash("sha256").update(text).digest("hex");

async function interpreter(document) {
  const folder = vscode.workspace.getWorkspaceFolder(document.uri);
  const config = vscode.workspace.getConfiguration("pysx", document.uri);
  const candidates = [config.get("pythonPath")];
  const python = vscode.extensions.getExtension("ms-python.python");
  if (python) {
    const api = await python.activate();
    const active = api.environments?.getActiveEnvironmentPath(document.uri);
    const resolved = active && await api.environments.resolveEnvironment(active);
    candidates.push(resolved?.executable?.uri?.fsPath);
  }
  const configured = vscode.workspace.getConfiguration("python", document.uri).get("defaultInterpreterPath");
  if (configured) candidates.push(configured.replaceAll("${workspaceFolder}", folder?.uri.fsPath ?? ""));
  for (const item of vscode.workspace.workspaceFolders ?? []) {
    candidates.push(path.join(item.uri.fsPath, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python"));
  }
  return candidates.find((item) => item && fs.existsSync(item) && fs.statSync(item).isFile());
}

function mappedOffsets(model, start, end, diagnostic = false) {
  const slice = model.map.slice(start, end);
  const spans = diagnostic ? slice.filter((span) => span != null) : slice;
  if (!spans.length || spans.some((span) => span == null)) return null;
  let first = spans[0][0], last = spans[0][1];
  for (const [begin, finish] of spans.slice(1)) {
    if (begin < first || begin > last) return null;
    last = Math.max(last, finish);
  }
  return [first, last];
}

function generatedOffsetAt(model, position) {
  let start = 0;
  for (let row = 0; row < position.line; row++) {
    const next = model.text.indexOf("\n", start);
    if (next < 0) return model.text.length;
    start = next + 1;
  }
  let end = model.text.indexOf("\n", start);
  if (end < 0) end = model.text.length;
  if (model.text[end - 1] === "\r") end--;
  return Math.min(start + position.character, end);
}

exports.activate = function (context) {
  const collection = vscode.languages.createDiagnosticCollection("pysx");
  const output = vscode.window.createOutputChannel("pysx");
  const states = new Map(), entries = new Map(), pending = new Map(), children = new Set(), inflight = new Map(), timers = new Map(), directories = new Set();
  let sequence = 0, disposed = false;
  context.subscriptions.push(collection, output);

  function stop(child) {
    try {
      if (process.platform !== "win32" && child.pid) process.kill(-child.pid, "SIGTERM");
      else child.kill();
    } catch (error) { if (error.code !== "ESRCH") output.appendLine(String(error)); }
  }

  function worker(python, args, root, input, token) {
    return new Promise((resolve, reject) => {
      const child = execFile(python, args, { cwd: root, detached: process.platform !== "win32", maxBuffer: 128 * 1024 * 1024, timeout: args.includes("pysx.editor_model") ? 180000 : 30000 }, (error, stdout, stderr) => {
        children.delete(child);
        if (error?.killed) stop(child);
        if (disposed || pending.get(root) !== token) return resolve(null);
        if (error) return reject(new Error(stderr || error.message));
        try { resolve(JSON.parse(stdout)); } catch (failure) { reject(failure); }
      });
      children.add(child);
      const job = inflight.get(root);
      if (job?.token === token) job.child = child;
      child.stdin.end(input ?? "");
    });
  }

  async function ignoreMirrorDiagnostics(document) {
    if (!vscode.workspace.getWorkspaceFolder(document.uri)) return;
    const pylance = vscode.extensions.getExtension("ms-python.vscode-pylance");
    if (!pylance) return;
    await pylance.activate();
    const python = vscode.workspace.getConfiguration("python", document.uri);
    const ignored = python.get("analysis.ignore", []), pattern = "**/_pysx_revision_*/**";
    if (!ignored.includes(pattern)) await python.update("analysis.ignore", [...ignored, pattern], vscode.ConfigurationTarget.WorkspaceFolder);
  }

  function valid(state) {
    if (!state?.ready || states.get(state.root) !== state) return false;
    const sources = new Map(vscode.workspace.textDocuments.filter(managed).map((doc) => [canonical(doc.fileName), doc]));
    return state.models.every((model) => {
      const source = sources.get(model.filename);
      return source ? source.getText() === model.source && (model.version < 0 || source.version === model.version)
        : fs.existsSync(model.filename) && digest(fs.readFileSync(model.filename, "utf8")) === model.digest;
    });
  }

  async function sourceDocument(model) {
    return vscode.workspace.textDocuments.find((doc) => managed(doc) && canonical(doc.fileName) === model.filename)
      ?? vscode.workspace.openTextDocument(vscode.Uri.file(model.original ?? model.filename));
  }

  function publish(entry) {
    const { state, model, source } = entry;
    if (!valid(state) || source.isClosed || source.languageId !== "python") return;
    const diagnostics = model.diagnostics.map((item) => {
      const value = new vscode.Diagnostic(new vscode.Range(item.line, item.startChar, item.endLine ?? item.line, item.endChar), item.message,
        item.severity === "warning" ? vscode.DiagnosticSeverity.Warning : vscode.DiagnosticSeverity.Error);
      value.source = "pysx";
      return value;
    });
    for (const raw of state.typeDiagnostics.get(canonical(model.target)) ?? []) {
      const item = new vscode.Diagnostic(new vscode.Range(raw.range.start.line, raw.range.start.character, raw.range.end.line, raw.range.end.character), raw.message,
        raw.severity === "error" ? vscode.DiagnosticSeverity.Error : vscode.DiagnosticSeverity.Warning);
      item.code = raw.rule;
      const code = String(item.code?.value ?? item.code);
      if (!["reportArgumentType", "reportCallIssue", "reportReturnType", "reportGeneralTypeIssues"].includes(code)) continue;
      const start = generatedOffsetAt(model, item.range.start), end = generatedOffsetAt(model, item.range.end);
      if (!model.validation.some(([begin, finish]) => begin <= start && end <= finish)) continue;
      let span = mappedOffsets(model, start, end, true);
      if (!span) {
        const validation = model.validation.find(([begin, finish]) => begin <= start && end <= finish);
        const origin = model.map.slice(...validation).find((value) => value != null);
        if (origin) span = origin;
      }
      if (!span) continue;
      const value = new vscode.Diagnostic(new vscode.Range(source.positionAt(span[0]), source.positionAt(span[1])), item.message, item.severity);
      value.source = "pysx"; value.code = item.code;
      if (vscode.languages.getDiagnostics(source.uri).some((other) => other.source !== "pysx" && other.range.isEqual(value.range) && String(other.code?.value ?? other.code) === code)) continue;
      if (!diagnostics.some((other) => other.range.isEqual(value.range) && other.message === value.message)) diagnostics.push(value);
    }
    const fingerprint = JSON.stringify(diagnostics.map((item) => [item.message, item.code, item.range, item.severity]));
    if (entry.lastPublished === fingerprint) return;
    entry.lastPublished = fingerprint;
    collection.set(source.uri, diagnostics);
  }

  async function release(state) {
    if (!state) return;
    for (const model of state.models) {
      const entry = entries.get(canonical(model.target));
      entries.delete(canonical(model.target));
      if (entry?.document && !entry.document.isClosed) await vscode.languages.setTextDocumentLanguage(entry.document, "plaintext");
    }
    fs.rmSync(state.directory, { recursive: true, force: true });
    fs.rmSync(state.directory + ".json", { force: true });
    directories.delete(state.directory);
  }

  async function buildSnapshot(document, root, buffers, token) {
    const python = await interpreter(document);
    if (!python) throw new Error("select Python 3.14+ with pysx and Ruff installed");
    const revision = `_pysx_revision_${crypto.randomBytes(8).toString("hex")}`;
    const existing = states.get(root);
    const previous = states.get(root)?.models;
    const models = await worker(python, ["-m", "pysx.editor_model"], root, JSON.stringify({ root, revision, buffers, previous }), token);
    if (!models || disposed || pending.get(root) !== token) return null;
    for (const [filename, buffer] of Object.entries(buffers)) {
      const current = vscode.workspace.textDocuments.find((doc) => canonical(doc.fileName) === filename && managed(doc));
      if (!current || current.version !== buffer.version) return null;
    }
    const folderRoot = vscode.workspace.getWorkspaceFolder(document.uri)?.uri.fsPath ?? path.dirname(document.fileName);
    const directory = path.join(folderRoot, revision);
    fs.mkdirSync(directory, { recursive: true });
    directories.add(directory);
    fs.writeFileSync(path.join(directory, "__init__.py"), "");
    const state = { root, directory, models, token, ready: false, typeDiagnostics: new Map() };
    for (const model of models) {
      model.target = path.join(directory, model.relative);
      model.original = path.join(folderRoot, path.relative(root, model.filename));
      fs.mkdirSync(path.dirname(model.target), { recursive: true });
      fs.writeFileSync(model.target, model.text);
    }
    let types;
    try { types = await worker(python, ["-m", "pysx.editor_types", directory, root], root, undefined, token); }
    catch (error) { await release(state); throw error; }
    if (!types || disposed || pending.get(root) !== token) { await release(state); return null; }
    for (const item of types.generalDiagnostics) {
      const filename = canonical(item.file);
      const list = state.typeDiagnostics.get(filename) ?? [];
      list.push(item); state.typeDiagnostics.set(filename, list);
    }
    try { await ignoreMirrorDiagnostics(document); }
    catch (error) { await release(state); throw error; }
    if (disposed || pending.get(root) !== token) { await release(state); return null; }
    states.set(root, state);
    for (const model of models) {
      if (disposed || pending.get(root) !== token) {
        await release(state);
        await release(existing);
        return null;
      }
      const source = vscode.workspace.textDocuments.find((doc) => managed(doc) && canonical(doc.fileName) === model.filename);
      entries.set(canonical(model.target), { state, model, source });
    }
    if (disposed || pending.get(root) !== token) {
      await release(state);
      await release(existing);
      return null;
    }
    state.ready = true;
    await release(existing);
    if (!valid(state)) return null;
    for (const model of models) {
      const entry = entries.get(canonical(model.target));
      if (entry?.source) publish(entry);
    }
    return state;
  }

  async function refresh(document) {
    if (!managed(document)) return null;
    const root = canonical(vscode.workspace.getWorkspaceFolder(document.uri)?.uri.fsPath ?? path.dirname(document.fileName));
    const existing = states.get(root);
    if (valid(existing)) {
      const model = existing.models.find((item) => item.filename === canonical(document.fileName));
      const entry = model && entries.get(canonical(model.target));
      if (entry && entry.source !== document) { entry.source = document; entry.lastPublished = undefined; publish(entry); }
      return existing;
    }
    const buffers = {};
    for (const doc of vscode.workspace.textDocuments) {
      const filename = canonical(doc.fileName);
      if (managed(doc) && (filename === root || filename.startsWith(root + path.sep))) buffers[filename] = { source: doc.getText(), version: doc.version };
    }
    const key = digest(JSON.stringify(buffers));
    const previous = inflight.get(root);
    if (previous?.key === key) return previous.promise;
    const token = ++sequence;
    pending.set(root, token);
    if (previous?.child) stop(previous.child);
    const job = { key, token };
    inflight.set(root, job);
    job.promise = buildSnapshot(document, root, buffers, token).finally(() => {
      if (inflight.get(root) === job) inflight.delete(root);
    });
    return job.promise;
  }

  async function projected(document, position, command) {
    const state = await refresh(document);
    if (!state || !valid(state)) return null;
    const model = state.models.find((item) => item.filename === canonical(document.fileName));
    if (!model) return null;
    const offset = document.offsetAt(position);
    const site = model.sites.find((item) => item.start <= offset && offset <= item.end);
    if (!site) return null;
    const entry = entries.get(canonical(model.target));
    if (command === "vscode.executeReferenceProvider") await openAllMirrors(state);
    entry.document ??= await vscode.workspace.openTextDocument(model.target);
    if (!valid(state)) return null;
    const target = entry.document.positionAt(site.generated + offset - site.start);
    const result = await vscode.commands.executeCommand(command, entry.document.uri, target);
    return valid(state) ? { state, model, site, entry, result } : null;
  }

  async function locations(result, state) {
    const output = [], seen = new Set();
    for (const item of result ?? []) {
      const uri = item.uri ?? item.targetUri, range = item.range ?? item.targetSelectionRange;
      const entry = entries.get(canonical(uri.fsPath));
      let location;
      if (entry) {
        if (entry.state !== state) return [];
        const span = mappedOffsets(entry.model, generatedOffsetAt(entry.model, range.start), generatedOffsetAt(entry.model, range.end));
        if (!span) continue;
        const source = await sourceDocument(entry.model);
        location = new vscode.Location(source.uri, new vscode.Range(source.positionAt(span[0]), source.positionAt(span[1])));
      } else if (!generated(uri.fsPath)) location = new vscode.Location(uri, range);
      if (!location) continue;
      const key = location.uri.toString() + JSON.stringify(location.range);
      if (!seen.has(key)) { seen.add(key); output.push(location); }
    }
    return valid(state) ? output : [];
  }

  context.subscriptions.push(vscode.languages.registerHoverProvider(selector, {
    async provideHover(doc, position) {
      if (!managed(doc)) return undefined;
      const value = await projected(doc, position, "vscode.executeHoverProvider");
      return value?.result?.length ? new vscode.Hover(value.result.flatMap((item) => item.contents), new vscode.Range(doc.positionAt(value.site.start), doc.positionAt(value.site.end))) : undefined;
    },
  }));

  async function openAllMirrors(state) {
    for (const model of state.models) {
      if (!valid(state)) return;
      const entry = entries.get(canonical(model.target));
      entry.document ??= await vscode.workspace.openTextDocument(model.target);
    }
  }
  for (const [register, method, command] of [
    ["registerDefinitionProvider", "provideDefinition", "vscode.executeDefinitionProvider"],
    ["registerReferenceProvider", "provideReferences", "vscode.executeReferenceProvider"],
  ]) context.subscriptions.push(vscode.languages[register](selector, {
    async [method](doc, position) {
      if (!managed(doc)) return undefined;
      const value = await projected(doc, position, command);
      return value ? locations(value.result, value.state) : undefined;
    },
  }));
  context.subscriptions.push(vscode.languages.registerCompletionItemProvider(selector, {
    async provideCompletionItems(doc, position) {
      if (!managed(doc)) return undefined;
      const value = await projected(doc, position, "vscode.executeCompletionItemProvider");
      if (!value?.result) return undefined;
      if (value.site.role === "prop") {
        const help = await vscode.commands.executeCommand("vscode.executeSignatureHelpProvider", value.entry.document.uri, value.entry.document.positionAt(value.site.generated));
        value.result.items = (help?.signatures ?? []).flatMap((signature) => signature.parameters.map((parameter) => {
          const label = typeof parameter.label === "string" ? parameter.label : signature.label.slice(...parameter.label);
          const name = label.split(":")[0].split("=")[0].trim().replace(/^\*+/, "");
          const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Property); item.detail = label;
          return item;
        })).filter((item) => /^[A-Za-z_]\w*$/.test(item.label));
        if (value.site.props) value.result.items = value.site.props.map((name) => new vscode.CompletionItem(name, vscode.CompletionItemKind.Property));
      }
      for (const item of value.result.items) {
        if (value.site.native && typeof item.label === "string") { item.label = item.label.toLowerCase(); item.insertText = item.label; }
        item.range = new vscode.Range(doc.positionAt(value.site.start), doc.positionAt(value.site.end));
        item.textEdit = undefined; item.additionalTextEdits = undefined;
      }
      return valid(value.state) ? value.result : undefined;
    },
  }));

  context.subscriptions.push(vscode.commands.registerCommand("pysx.rename", async (newName) => {
    const editor = vscode.window.activeTextEditor;
    if (!managed(editor?.document)) return false;
    const doc = editor.document, state = await refresh(doc);
    if (!state || !valid(state) || state.models.some((model) => !model.complete)) return false;
    const model = state.models.find((item) => item.filename === canonical(doc.fileName));
    const offset = doc.offsetAt(editor.selection.active);
    const site = model.sites.find((item) => item.start <= offset && offset <= item.end);
    const generatedOffset = site ? site.generated + offset - site.start : model.map.findIndex((span) => span && span[0] === offset);
    if (generatedOffset < 0) return false;
    newName ??= await vscode.window.showInputBox({ prompt: "Rename Python symbol and matching markup", validateInput: (value) => /^[A-Za-z_]\w*$/.test(value) ? null : "Enter a Python identifier" });
    if (!newName) return false;
    const entry = entries.get(canonical(model.target));
    await openAllMirrors(state);
    if (!valid(state)) return false;
    const backend = await vscode.commands.executeCommand("vscode.executeDocumentRenameProvider", entry.document.uri, entry.document.positionAt(generatedOffset), newName);
    if (!backend || !valid(state)) return false;
    const edit = new vscode.WorkspaceEdit(), seen = new Set(), ranges = new Map();
    for (const [uri, changes] of backend.entries()) {
      const target = entries.get(canonical(uri.fsPath));
      if (!target || target.state !== state) return false;
      const source = await sourceDocument(target.model);
      for (const change of changes) {
        const span = mappedOffsets(target.model, generatedOffsetAt(target.model, change.range.start), generatedOffsetAt(target.model, change.range.end));
        if (!span) return false;
        const key = source.uri.toString() + JSON.stringify(span) + change.newText;
        if (seen.has(key)) continue;
        seen.add(key);
        const previous = ranges.get(source.uri.toString()) ?? [];
        if (previous.some(([start, end]) => start < span[1] && span[0] < end)) return false;
        previous.push(span); ranges.set(source.uri.toString(), previous);
        edit.replace(source.uri, new vscode.Range(source.positionAt(span[0]), source.positionAt(span[1])), change.newText);
      }
    }
    return valid(state) && vscode.workspace.applyEdit(edit);
  }));

  context.subscriptions.push(vscode.commands.registerCommand("pysx.applyImports", async (uri, version, token) => {
    const doc = await vscode.workspace.openTextDocument(uri), state = await refresh(doc);
    if (!state || state.token !== token || !valid(state) || doc.version !== version) return false;
    const model = state.models.find((item) => item.filename === canonical(doc.fileName));
    if (!model?.complete) return false;
    const edit = new vscode.WorkspaceEdit();
    for (const [start, end, text] of model.edits) edit.replace(doc.uri, new vscode.Range(doc.positionAt(start), doc.positionAt(end)), text);
    return valid(state) && doc.version === version && vscode.workspace.applyEdit(edit);
  }));
  context.subscriptions.push(vscode.languages.registerCodeActionsProvider(selector, {
    async provideCodeActions(doc) {
      if (!managed(doc)) return [];
      const state = await refresh(doc);
      const model = state?.models.find((item) => item.filename === canonical(doc.fileName));
      if (!model?.complete || !model.edits.length || !valid(state)) return [];
      const action = new vscode.CodeAction("pysx: remove unused imports", vscode.CodeActionKind.Source.append("organizeImports.pysx"));
      action.command = { title: action.title, command: "pysx.applyImports", arguments: [doc.uri, doc.version, state.token] };
      return [action];
    },
  }, { providedCodeActionKinds: [vscode.CodeActionKind.Source.append("organizeImports.pysx")] }));

  context.subscriptions.push(vscode.commands.registerCommand("pysx.configureTooling", async () => {
    const fail = (message) => {
      output.appendLine(message);
      output.show(true);
      void vscode.window.showWarningMessage(message);
      return false;
    };
    try {
    const doc = vscode.window.activeTextEditor?.document;
    if (!managed(doc)) return fail("pysx: Open a Python file in your workspace before configuring template-aware imports.");
    if (!vscode.workspace.getWorkspaceFolder(doc.uri)) return fail("pysx: Open this Python file in a workspace folder before configuring template-aware imports.");
    output.appendLine(`Configuring template-aware imports for ${doc.fileName}; waiting for workspace analysis.`);
    output.show(true);
    const state = await refresh(doc);
    if (!state || !valid(state)) return fail("pysx: Workspace analysis was interrupted or changed. Wait for analysis to finish, then run Configure Template-Aware Imports again.");
    const model = state.models.find((item) => item.filename === canonical(doc.fileName));
    if (!model) return fail("pysx: The active file was not included in workspace analysis. Open a maintained Python source file and try again.");
    if (!model.complete) {
      for (const diagnostic of model.diagnostics) output.appendLine(diagnostic.message);
      return fail("pysx: The active file could not be completely analyzed. Resolve its pysx diagnostics before configuring template-aware imports.");
    }
    await vscode.extensions.getExtension("ms-python.vscode-pylance")?.activate();
    const target = vscode.ConfigurationTarget.WorkspaceFolder;
    const python = vscode.workspace.getConfiguration("python", doc.uri);
    const severity = python.get("analysis.diagnosticSeverityOverrides", {});
    await python.update("analysis.ignore", [...new Set([...python.get("analysis.ignore", []), "**/_pysx_revision_*/**"])], target);
    await python.update("analysis.diagnosticSeverityOverrides", { ...severity, reportUnusedImport: "none", reportUnusedVariable: "none" }, target);
    await python.update("analysis.disableTaggedHints", true, target);
    await python.update("analysis.fixAll", python.get("analysis.fixAll", []).filter((kind) => kind !== "source.unusedImports"), target);
    const editor = vscode.workspace.getConfiguration("editor", doc.uri);
    const actions = editor.get("codeActionsOnSave", {});
    await editor.update("codeActionsOnSave", {
      ...actions,
      "source.fixAll": "never", "source.fixAll.pylance": "never", "source.fixAll.ruff": "never",
      "source.organizeImports": "never", "source.organizeImports.ruff": "never", "source.unusedImports": "never",
      "source.organizeImports.pysx": "explicit",
    }, target);
    const ruffExtension = vscode.extensions.getExtension("charliermarsh.ruff");
    if (ruffExtension) {
      await ruffExtension.activate();
      const ruff = vscode.workspace.getConfiguration("ruff", doc.uri);
      await ruff.update("lint.ignore", [...new Set([...(ruff.get("lint.ignore") ?? []), "F401", "F811", "F821", "F823", "F841", "TC"])], target);
    }
    const message = "pysx: Template-aware imports configured for this workspace folder.";
    output.appendLine(message);
    void vscode.window.showInformationMessage(message);
    return true;
    } catch (error) {
      return fail(`pysx: Could not configure template-aware imports: ${error.message ?? String(error)}`);
    }
  }));

  const run = (doc) => {
    if (!managed(doc)) return;
    const root = canonical(vscode.workspace.getWorkspaceFolder(doc.uri)?.uri.fsPath ?? path.dirname(doc.fileName));
    clearTimeout(timers.get(root));
    for (const item of vscode.workspace.textDocuments) {
      if (managed(item) && canonical(item.fileName).startsWith(root + path.sep)) collection.delete(item.uri);
    }
    timers.set(root, setTimeout(() => {
      timers.delete(root);
      refresh(doc).catch((error) => output.appendLine(String(error)));
    }, 150));
  };
  const watcher = vscode.workspace.createFileSystemWatcher("**/*.py");
  const configWatcher = vscode.workspace.createFileSystemWatcher("**/{pyproject.toml,pyrightconfig.json,ruff.toml,.ruff.toml}");
  const changedOnDisk = (uri) => {
    if (generated(uri.fsPath)) return;
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (!folder) return;
    const root = canonical(folder.uri.fsPath), state = states.get(root);
    const job = inflight.get(root);
    if (job) {
      pending.set(root, ++sequence);
      if (job.child) stop(job.child);
      inflight.delete(root);
    }
    if (state) state.ready = false;
    const doc = vscode.workspace.textDocuments.find((item) => managed(item) && canonical(item.fileName).startsWith(root + path.sep));
    if (doc) run(doc);
  };
  context.subscriptions.push(
    vscode.commands.registerCommand("pysx.refresh", () => refresh(vscode.window.activeTextEditor?.document)),
    vscode.workspace.onDidOpenTextDocument(run),
    vscode.workspace.onDidSaveTextDocument(run),
    vscode.workspace.onDidChangeTextDocument((event) => run(event.document)),
    vscode.workspace.onDidCloseTextDocument((doc) => { collection.delete(doc.uri); if (managed(doc)) changedOnDisk(doc.uri); }),
    watcher, watcher.onDidCreate(changedOnDisk), watcher.onDidChange(changedOnDisk), watcher.onDidDelete(changedOnDisk),
    configWatcher, configWatcher.onDidCreate(changedOnDisk), configWatcher.onDidChange(changedOnDisk), configWatcher.onDidDelete(changedOnDisk),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (!event.affectsConfiguration("pysx.pythonPath") && !event.affectsConfiguration("python.defaultInterpreterPath")) return;
      for (const state of states.values()) state.ready = false;
      vscode.workspace.textDocuments.forEach(run);
    }),
    vscode.languages.onDidChangeDiagnostics((event) => { for (const uri of event.uris) {
      if (generated(uri.fsPath)) continue;
      for (const state of states.values()) {
        const model = state.models.find((item) => item.filename === canonical(uri.fsPath));
        const entry = model && entries.get(canonical(model.target));
        if (entry?.source) publish(entry);
      }
    } }),
    vscode.window.onDidChangeActiveTextEditor((editor) => vscode.commands.executeCommand("setContext", "pysx.managedDocument", managed(editor?.document))),
    { dispose() { disposed = true; for (const timer of timers.values()) clearTimeout(timer); for (const child of children) stop(child); for (const directory of directories) { fs.rmSync(directory, { recursive: true, force: true }); fs.rmSync(directory + ".json", { force: true }); } directories.clear(); states.clear(); entries.clear(); } },
  );
  vscode.commands.executeCommand("setContext", "pysx.managedDocument", managed(vscode.window.activeTextEditor?.document));
  vscode.workspace.textDocuments.forEach(run);
  return { refresh };
};
