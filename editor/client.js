const vscode = require("vscode");
const os = require("node:os");
const { execFile } = require("node:child_process");
const { LanguageClient, State, CloseAction, ErrorAction } = require("vscode-languageclient/node");

const MAX_RESTARTS = 4;
// Every document-scoped provider the server can advertise. One client per
// workspace folder sees every Python file, so ownership is enforced here
// rather than through overlapping document selectors.
const GUARDED = [
  "provideCodeActions", "provideCodeLenses", "provideColorPresentations", "provideCompletionItem",
  "provideDeclaration", "provideDefinition", "provideDiagnostics", "provideDocumentColors",
  "provideDocumentFormattingEdits", "provideDocumentHighlights", "provideDocumentLinks",
  "provideDocumentRangeFormattingEdits", "provideDocumentRangesFormattingEdits",
  "provideDocumentSemanticTokens", "provideDocumentSemanticTokensEdits",
  "provideDocumentRangeSemanticTokens", "provideDocumentSymbols", "provideFoldingRanges",
  "provideHover", "provideImplementation", "provideInlayHints", "provideInlineValues",
  "provideLinkedEditingRange", "provideOnTypeFormattingEdits", "provideReferences",
  "provideRenameEdits", "provideSelectionRanges", "provideSignatureHelp", "provideTypeDefinition",
  "prepareCallHierarchy", "prepareRename", "prepareTypeHierarchy",
];
let active = null;

async function interpreter(resource) {
  const configured = vscode.workspace.getConfiguration("pysx", resource).get("pythonPath");
  if (configured) return configured;
  const python = vscode.extensions.getExtension("ms-python.python");
  if (!python) return null;
  const api = await python.activate();
  const selected = api.environments?.getActiveEnvironmentPath?.(resource);
  if (!selected) return null;
  const resolved = await api.environments.resolveEnvironment(selected);
  // The selected path may name an environment directory rather than its binary.
  return resolved?.executable?.uri?.fsPath ?? resolved?.path ?? selected.path ?? null;
}

function ownershipMiddleware(owns) {
  const held = (document) => Boolean(document) && owns(document);
  const middleware = {
    didOpen: (document, next) => (held(document) ? next(document) : Promise.resolve()),
    didChange: (event, next) => (held(event.document) ? next(event) : Promise.resolve()),
    didSave: (document, next) => (held(document) ? next(document) : Promise.resolve()),
    didClose: (document, next) => (held(document) ? next(document) : Promise.resolve()),
    willSave: (event, next) => (held(event.document) ? next(event) : Promise.resolve()),
  };

  for (const name of GUARDED) {
    middleware[name] = (...args) => {
      const next = args[args.length - 1];
      const target = args[0];
      const document = target && typeof target.languageId === "string"
        ? target
        : vscode.workspace.textDocuments.find((item) => item.uri.toString() === String(target));
      return held(document) ? next(...args.slice(0, -1)) : undefined;
    };
  }

  return middleware;
}

function probe(python, args) {
  return new Promise((resolve) => {
    // A neutral directory keeps the probe about the selected environment
    // instead of whatever directory the editor happens to have been started in.
    execFile(python, args, { timeout: 30000, cwd: os.tmpdir() }, (error, stdout) => {
      resolve(error ? null : stdout.trim());
    });
  });
}

function createClient({ python, output, serverOptions, documentSelector, workspaceFolder, owns,
  watched }) {
  const watcher = vscode.workspace.createFileSystemWatcher(watched
    ? new vscode.RelativePattern(watched, "**/{*.py,pyproject.toml,pyrightconfig.json}")
    : "**/{*.py,pyproject.toml,pyrightconfig.json}");
  let client;
  let highlighting;
  let child;
  let tail = Promise.resolve();
  let restartPromise;
  let disposePromise;
  let recoveryTimer;
  let revision = 0;
  let restarts = 0;
  let starts = 0;
  let stops = 0;
  let running = false;
  let intentional = false;
  let disposed = false;
  let exhausted = false;
  let lastError = null;

  function enqueue(operation) {
    const result = tail.then(operation);
    tail = result.catch((error) => { lastError = String(error.message ?? error); });
    return result;
  }
  function report(error) {
    output.error(`pysx lifecycle: ${error.message ?? error}`);
  }
  function cancelRecovery() {
    revision += 1;
    clearTimeout(recoveryTimer);
    recoveryTimer = undefined;
  }
  function recover(reason) {
    if (disposed || intentional || !running || recoveryTimer) return;
    running = false;
    highlighting?.pause();
    const restart = restarts < MAX_RESTARTS;
    if (!restart) {
      exhausted = true;
      const message = "pysx: the language server exhausted its four automatic restarts. " +
        "Run `pysx: Restart Language Server` to recover after fixing the environment.";
      output.error(message);
      void vscode.window.showErrorMessage(message);
    } else {
      restarts += 1;
      output.appendLine(`pysx: recovering after ${reason} (attempt ${restarts}/${MAX_RESTARTS}).`);
    }
    const requested = revision;
    // The supported close handler is awaited by the library. Its cleanup must
    // finish before our serialized owner starts the next connection.
    recoveryTimer = setTimeout(() => {
      recoveryTimer = undefined;
      void enqueue(async () => {
        if (disposed || requested !== revision) return;
        intentional = true;
        try {
          await stopOwned();
          if (restart) await startOwned();
        } finally {
          intentional = false;
        }
      }).catch(report);
    }, 0);
  }
  function makeClient() {
    return new LanguageClient(
      "pysx", "pysx language server",
      serverOptions ?? { command: python, args: ["-m", "pysx.tooling.lsp.lsp"], options: { env: process.env } },
      {
        documentSelector: documentSelector ?? [{ language: "python", scheme: "file" }],
        workspaceFolder,
        outputChannel: output,
        initializationOptions: { pythonPath: python, markupHighlighting: "decorations" },
        synchronize: { fileEvents: watcher },
        middleware: owns ? ownershipMiddleware(owns) : undefined,
        errorHandler: {
          error(_error, _message, count) {
            if (count > 3) recover("repeated connection errors");
            return { action: ErrorAction.Continue, handled: true };
          },
          closed() {
            recover("a closed connection");
            return { action: CloseAction.DoNotRestart, handled: true,
              message: "pysx: connection closed; recovery is owned by the client lifecycle." };
          },
        },
      },
    );
  }
  async function exited(process) {
    if (!process || process.exitCode !== null || process.signalCode !== null) return;
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => process.kill("SIGKILL"), 2000);
      const deadline = setTimeout(() => {
        process.removeListener("exit", done);
        reject(new Error("An owned language-server process did not exit"));
      }, 5000);
      function done() {
        clearTimeout(timer);
        clearTimeout(deadline);
        resolve();
      }
      process.once("exit", done);
    });
  }
  async function stopOwned() {
    running = false;
    highlighting?.pause();
    const owned = child ?? client.serverProcess;
    try {
      if (client.isRunning()) {
        await client.stop(10000);
        stops += 1;
      } else if (client.state === State.StartFailed) {
        owned?.kill();
      }
    } finally {
      await exited(owned);
      child = undefined;
    }
  }
  async function startOwned() {
    if (disposed || client.isRunning()) return;
    if (client.state === State.StartFailed) {
      await stopOwned();
      highlighting?.dispose();
      highlighting = undefined;
      await client.dispose().catch(() => {});
      client = makeClient();
    }
    try {
      await client.start();
      child = client.serverProcess;
      if (disposed) {
        await stopOwned();
        return;
      }
      starts += 1;
      running = true;
      exhausted = false;
      lastError = null;
      if (!highlighting) {
        highlighting = require("./highlighting.js").activate(client, output, owns ?? (() => true));
      }
      else highlighting.resume();
      output.appendLine(`pysx: language server started with ${python}.`);
    } catch (error) {
      lastError = String(error.message ?? error);
      await stopOwned();
      throw error;
    }
  }
  client = makeClient();
  return {
    get client() { return client; },
    get highlighting() { return highlighting; },
    start: () => enqueue(startOwned),
    restart() {
      if (disposed) return Promise.resolve();
      if (restartPromise) return restartPromise;
      cancelRecovery();
      restarts = 0;
      exhausted = false;
      restartPromise = enqueue(async () => {
        if (disposed) return;
        intentional = true;
        try {
          await stopOwned();
          await startOwned();
        } finally {
          intentional = false;
        }
      });
      restartPromise.then(() => { restartPromise = undefined; }, () => { restartPromise = undefined; });
      return restartPromise;
    },
    stop() {
      cancelRecovery();
      return enqueue(async () => {
        intentional = true;
        try { await stopOwned(); }
        finally { intentional = false; }
      });
    },
    dispose() {
      if (disposePromise) return disposePromise;
      disposed = true;
      cancelRecovery();
      watcher.dispose();
      highlighting?.dispose();
      disposePromise = enqueue(async () => {
        await stopOwned();
        await client.dispose().catch((error) => {
          if (client.state !== State.StartFailed) throw error;
        });
      });
      return disposePromise;
    },
    status: () => ({ restarts, starts, stops, running, disposed, exhausted, lastError }),
  };
}

exports.createClient = createClient;

exports.activate = async function (context) {
  const output = vscode.window.createOutputChannel("pysx language server", { log: true });
  context.subscriptions.push(output);

  const router = require("./workspaces.js").createRouter({
    output,
    createClient,
    resolveInterpreter: interpreter,
    probeEnvironment: probe,
  });
  active = router;
  context.subscriptions.push(
    vscode.commands.registerCommand("pysx.restartServer", () =>
      router.restart(vscode.window.activeTextEditor?.document?.uri)),
    { dispose: () => { void router.dispose().catch((error) => output.error(String(error))); } },
  );
  const commands = require("./commands.js").register(context, { output, router });
  await router.start();
  const current = () => router.primary();

  return {
    get client() { return current()?.lifecycle?.client ?? null; },
    get highlighting() { return current()?.lifecycle?.highlighting ?? null; },
    get lifecycle() { return current()?.lifecycle ?? null; },
    get interpreter() { return current()?.python ?? null; },
    get ruff() { return current()?.ruff ?? null; },
    owner: (uri) => router.owner(uri),
    commands,
    workspaces: () => router.status(),
    router,
  };
};

exports.deactivate = async function () {
  const previous = active;
  active = null;
  await previous?.dispose();
};
