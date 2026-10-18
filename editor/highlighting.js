const vscode = require("vscode");

// TextMate owns direct literals. These theme-aware decorations cover statically
// resolved aliases/shared templates without registering a Python token provider.
exports.activate = function (client, output, owns = () => true) {
  const types = new Map(["type", "class", "property", "keyword", "variable", "string"]
    .map((kind) => [kind, vscode.window.createTextEditorDecorationType({
      color: new vscode.ThemeColor(`pysx.markup.${kind}`),
    })]));
  const timers = new Map();
  const serials = new Map();
  const results = new Map();
  let disposed = false;
  let paused = false;
  const key = (uri) => uri.toString();
  const eligible = (document) => !disposed && !paused && document.languageId === "python" &&
    document.uri.scheme === "file" && owns(document);
  function clear(uri) {
    const name = key(uri);
    clearTimeout(timers.get(name));
    timers.delete(name);
    serials.set(name, (serials.get(name) ?? 0) + 1);
    results.delete(name);
    for (const editor of vscode.window.visibleTextEditors) {
      if (key(editor.document.uri) === name) {
        for (const type of types.values()) editor.setDecorations(type, []);
      }
    }
  }
  async function refresh(document) {
    const name = key(document.uri);
    clear(document.uri);
    if (!eligible(document) || document.isClosed) return;
    const serial = serials.get(name);
    const version = document.version;
    try {
      const result = await client.sendRequest("pysx/markupTokens", {
        textDocument: { uri: name },
      });
      if (!eligible(document) || document.isClosed || serial !== serials.get(name) ||
          version !== document.version || result.version !== version) return;
      results.set(name, result);
      for (const editor of vscode.window.visibleTextEditors) {
        if (key(editor.document.uri) !== name) continue;
        for (const [kind, type] of types) {
          editor.setDecorations(type, result.tokens.filter((token) => token.kind === kind)
            .map(({ range }) => new vscode.Range(range.start.line, range.start.character,
              range.end.line, range.end.character)));
        }
      }
    } catch (error) {
      if (!disposed && serial === serials.get(name)) {
        output.warn(`pysx markup highlighting: ${error.message ?? error}`);
      }
    }
  }
  function schedule(document) {
    clear(document.uri);
    if (!eligible(document)) return;
    const name = key(document.uri);
    timers.set(name, setTimeout(() => {
      timers.delete(name);
      void refresh(document);
    }, 100));
  }
  const subscriptions = [
    client.onNotification("pysx/analysisChanged", ({ uris }) => {
      for (const editor of vscode.window.visibleTextEditors) {
        if (uris.includes(key(editor.document.uri))) schedule(editor.document);
      }
    }),
    vscode.workspace.onDidChangeTextDocument(({ document }) => schedule(document)),
    vscode.workspace.onDidOpenTextDocument(schedule),
    vscode.workspace.onDidCloseTextDocument((document) => {
      clear(document.uri);
      serials.delete(key(document.uri));
    }),
    vscode.window.onDidChangeVisibleTextEditors((editors) => {
      for (const editor of editors) schedule(editor.document);
    }),
  ];
  for (const editor of vscode.window.visibleTextEditors) schedule(editor.document);
  return {
    refresh,
    applied: (uri) => results.get(key(uri)),
    pause() {
      if (disposed) return;
      paused = true;
      for (const document of vscode.workspace.textDocuments) clear(document.uri);
    },
    resume() {
      if (disposed) return;
      paused = false;
      for (const editor of vscode.window.visibleTextEditors) schedule(editor.document);
    },
    dispose() {
      disposed = true;
      for (const subscription of subscriptions) subscription.dispose();
      for (const document of vscode.workspace.textDocuments) clear(document.uri);
      for (const timer of timers.values()) clearTimeout(timer);
      for (const type of types.values()) type.dispose();
      timers.clear();
      serials.clear();
      results.clear();
    },
  };
};
