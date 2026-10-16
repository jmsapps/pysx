const vscode = require("vscode");
const fs = require("node:fs");
const path = require("node:path");

const DETACHED = "";
const COALESCE = 150;

function canonical(fsPath) {
  try {
    return fs.realpathSync.native(fsPath);
  } catch {
    return path.resolve(fsPath);
  }
}

function within(root, target) {
  return target === root || target.startsWith(root.endsWith(path.sep) ? root : root + path.sep);
}

function folders() {
  return (vscode.workspace.workspaceFolders ?? []).map((folder) => ({
    folder,
    root: canonical(folder.uri.fsPath),
  }));
}

// VSCode reports the containing folder, but nested roots must resolve to the
// innermost one or two clients would claim the same document.
function ownerOf(uri) {
  if (!uri || uri.scheme !== "file") return null;
  const target = canonical(uri.fsPath);
  let owner = null;

  for (const candidate of folders()) {
    if (!within(candidate.root, target)) continue;
    if (!owner || candidate.root.length > owner.root.length) owner = candidate;
  }

  return owner;
}

exports.canonical = canonical;
exports.ownerOf = ownerOf;

exports.createRouter = function ({ output, createClient, resolveInterpreter, probeEnvironment }) {
  const entries = new Map();
  const timers = new Map();
  const probes = new Map();
  // Staleness is tracked per folder: one folder's reselection must never
  // cancel another folder's pending installation.
  const generations = new Map();
  let disposed = false;
  let tail = Promise.resolve();

  function bump(key) {
    const next = (generations.get(key) ?? 0) + 1;
    generations.set(key, next);
    return next;
  }
  function keyFor(uri) {
    const owner = ownerOf(uri);
    return owner ? owner.root : DETACHED;
  }
  function owns(key) {
    return (document) => !disposed && document?.uri?.scheme === "file" &&
      keyFor(document.uri) === key;
  }
  function enqueue(operation) {
    const result = tail.then(operation);
    tail = result.catch((error) => output.error(`pysx workspaces: ${error.message ?? error}`));
    return result;
  }
  function environment(python) {
    let pending = probes.get(python);

    if (!pending) {
      pending = (async () => ({
        server: await probeEnvironment(python, ["-c", "import pysx.lsp_server; print('ok')"]),
        tooling: await probeEnvironment(python, ["-m", "pysx.tooling"]),
      }))();
      probes.set(python, pending);
    }

    return pending;
  }
  async function install(key, folder, requested) {
    const python = await resolveInterpreter(folder?.uri);
    if (disposed || requested !== generations.get(key)) return;
    const existing = entries.get(key);

    if (!python || !fs.existsSync(python)) {
      await remove(key);
      entries.set(key, { key, folder, python: null, lifecycle: null, ruff: null,
        error: "pysx: no Python 3.14+ interpreter is selected for " +
          `${folder ? folder.name : "files outside a workspace"}. Select one, then retry.` });
      output.appendLine(entries.get(key).error);
      return;
    }

    if (existing?.python === python && existing.lifecycle) return;
    const probed = await environment(python);
    if (disposed || requested !== generations.get(key)) return;

    if (probed.server !== "ok") {
      await remove(key);
      entries.set(key, { key, folder, python, lifecycle: null, ruff: null,
        error: "pysx: the selected environment does not have the language server extra. " +
          "Install `pysx[lsp]` into it, then select it again. Diagnostics remain available " +
          "through `python -m pysx.check`." });
      output.appendLine(entries.get(key).error);
      return;
    }
    const resolved = probed.tooling && JSON.parse(probed.tooling);

    if (resolved?.error) output.appendLine(resolved.error);
    else if (resolved) output.appendLine(`pysx: using Ruff ${resolved.version} at ${resolved.path}`);
    await remove(key);
    const lifecycle = createClient({
      python,
      output,
      workspaceFolder: folder,
      owns: owns(key),
      watched: folder,
    });
    entries.set(key, { key, folder, python, lifecycle,
      ruff: resolved?.error ? null : resolved, error: null });
    await lifecycle.start();
  }
  async function remove(key, keep = false) {
    const existing = entries.get(key);
    if (!keep) entries.delete(key);
    if (existing?.lifecycle) await existing.lifecycle.dispose();
  }
  function schedule(key, folder) {
    const requested = bump(key);
    clearTimeout(timers.get(key));
    timers.set(key, setTimeout(() => {
      timers.delete(key);
      void enqueue(() => (disposed ? Promise.resolve() : install(key, folder, requested)));
    }, COALESCE));
  }
  function reconcile() {
    const present = folders();

    for (const key of [...entries.keys()]) {
      if (key !== DETACHED && !present.some((candidate) => candidate.root === key)) {
        clearTimeout(timers.get(key));
        timers.delete(key);
        void enqueue(() => remove(key));
      }
    }

    return enqueue(async () => {
      for (const candidate of present) {
        if (entries.has(candidate.root)) continue;
        await install(candidate.root, candidate.folder, bump(candidate.root));
      }
    });
  }
  function detach(document) {
    if (document.languageId !== "python" || document.uri.scheme !== "file") return;
    if (keyFor(document.uri) !== DETACHED || entries.has(DETACHED)) return;
    schedule(DETACHED, undefined);
  }

  const subscriptions = [
    vscode.workspace.onDidChangeWorkspaceFolders(() => { void reconcile(); }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      for (const [key, entry] of entries) {
        if (event.affectsConfiguration("pysx.pythonPath", entry.folder?.uri) ||
            event.affectsConfiguration("python.defaultInterpreterPath", entry.folder?.uri)) {
          schedule(key, entry.folder);
        }
      }
    }),
    vscode.workspace.onDidOpenTextDocument(detach),
  ];

  async function subscribeEnvironments() {
    const python = vscode.extensions.getExtension("ms-python.python");
    if (!python) return;
    const api = await python.activate();
    const changed = api.environments?.onDidChangeActiveEnvironmentPath;
    if (!changed) return;
    subscriptions.push(changed(({ resource }) => {
      const root = resource ? canonical(resource.uri.fsPath) : null;

      for (const [key, entry] of entries) {
        if (!root || key === root) schedule(key, entry.folder);
      }
    }));
  }

  return {
    async start() {
      await subscribeEnvironments();
      await reconcile();
      for (const document of vscode.workspace.textDocuments) detach(document);
    },
    owner(uri) { return entries.get(keyFor(uri)) ?? null; },
    primary() {
      const active = vscode.window.activeTextEditor?.document?.uri;
      const owner = active ? entries.get(keyFor(active)) : null;
      if (owner?.lifecycle) return owner;
      for (const entry of entries.values()) if (entry.lifecycle) return entry;
      return entries.values().next().value ?? null;
    },
    status() {
      return [...entries.values()].map((entry) => ({
        root: entry.key || null,
        name: entry.folder?.name ?? null,
        python: entry.python,
        error: entry.error,
        ...(entry.lifecycle ? entry.lifecycle.status() : { running: false }),
      }));
    },
    refresh(uri) {
      const key = keyFor(uri);
      const entry = entries.get(key);
      schedule(key, entry?.folder ?? ownerOf(uri)?.folder);
      return settled();
    },
    async restart(uri) {
      const key = keyFor(uri);
      const entry = entries.get(key);
      const folder = entry?.folder ?? ownerOf(uri)?.folder;
      const requested = bump(key);
      clearTimeout(timers.get(key));
      timers.delete(key);
      await enqueue(async () => {
        if (disposed || requested !== generations.get(key)) return;
        const python = await resolveInterpreter(folder?.uri);

        if (python && entry?.lifecycle && python === entry.python) {
          await entry.lifecycle.restart();
          return;
        }
        probes.delete(python);
        await install(key, folder, requested);
      });
    },
    dispose() {
      disposed = true;
      for (const subscription of subscriptions) subscription.dispose();
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
      // Disposed entries stay addressable so a host can still inspect the
      // final lifecycle state after deactivation.
      return enqueue(async () => {
        for (const key of [...entries.keys()]) await remove(key, true);
      });
    },
  };

  function settled() {
    return new Promise((resolve) => setTimeout(() => resolve(enqueue(() => {})), COALESCE + 20));
  }
};
