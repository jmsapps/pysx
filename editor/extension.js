const path = require("node:path");
const fs = require("node:fs");
const { execFile } = require("node:child_process");
const vscode = require("vscode");

// Baked at package time: once installed, __dirname is the extensions folder,
// not the pysx checkout.
const BAKED = require("./pysx-root.json").root;

function pythonPath() {
  const candidates = [];
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    candidates.push(path.join(folder.uri.fsPath, "pysx", ".venv", "bin", "python"));
    candidates.push(path.join(folder.uri.fsPath, ".venv", "bin", "python"));
  }
  candidates.push(path.join(BAKED, ".venv", "bin", "python"));
  return candidates.find((p) => fs.existsSync(p)) ?? null;
}

function activate(context) {
  const collection = vscode.languages.createDiagnosticCollection("pysx");
  const output = vscode.window.createOutputChannel("pysx");
  context.subscriptions.push(collection, output);

  // Checks read the file from disk and race each other: an open-triggered run
  // can outlive the save-triggered run that follows it and publish pre-edit
  // results. Only the newest check per document may touch the collection.
  let sequence = 0;
  const pending = new Map();

  const run = (doc) => {
    if (!doc || doc.languageId !== "python" || doc.uri.scheme !== "file") return;
    const python = pythonPath();
    if (!python) {
      output.appendLine("no pysx .venv found; diagnostics disabled");
      return;
    }
    const key = doc.uri.toString();
    const token = ++sequence;
    pending.set(key, token);
    execFile(python, ["-m", "pysx.check", doc.uri.fsPath], (err, stdout, stderr) => {
      if (stderr) output.appendLine(stderr.trim());
      if (pending.get(key) !== token) return;
      if (!stdout) { collection.delete(doc.uri); return; }
      let items;
      try {
        items = JSON.parse(stdout);
      } catch {
        output.appendLine(`unparseable output: ${stdout.slice(0, 200)}`);
        return;
      }
      collection.set(doc.uri, items.map((d) => {
        const diag = new vscode.Diagnostic(
          new vscode.Range(d.line, d.startChar, d.line, d.endChar),
          d.message,
          d.severity === "warning"
            ? vscode.DiagnosticSeverity.Warning
            : vscode.DiagnosticSeverity.Error,
        );
        diag.source = "pysx";
        return diag;
      }));
    });
  };

  context.subscriptions.push(
    vscode.workspace.onDidOpenTextDocument(run),
    vscode.workspace.onDidSaveTextDocument(run),
    vscode.workspace.onDidCloseTextDocument((doc) => {
      pending.delete(doc.uri.toString());
      collection.delete(doc.uri);
    }),
  );
  vscode.workspace.textDocuments.forEach(run);
}

function deactivate() {}

module.exports = { activate, deactivate };
