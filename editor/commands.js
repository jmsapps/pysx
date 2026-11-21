const vscode = require("vscode");
const { execFile } = require("node:child_process");
const trace = require("./trace.js");
const setup = require("./setup.js");

exports.register = function (context, { output, router }) {
  const fail = (message) => {
    output.appendLine(message);
    output.show(true);
    void vscode.window.showWarningMessage(message);
    return false;
  };
  const note = (message) => {
    output.appendLine(message);
    void vscode.window.showInformationMessage(message);
    return true;
  };
  function active() {
    const document = vscode.window.activeTextEditor?.document;
    if (!document || document.languageId !== "python" || document.uri.scheme !== "file") {
      return { error: "pysx: Open a Python file before running this command." };
    }
    const owner = router.owner(document.uri);
    if (!owner?.lifecycle?.status().running) {
      return { error: owner?.error ?? "pysx: No language server is running for this file yet." };
    }
    return { document, owner };
  }
  // Setup, rollback and restoration are owned by the shared controller; this adapter
  // only reports what the resident server knows about the active document.
  const setupAdapter = {
    async resolve() {
        const selected = active();
        if (selected.error) return { error: selected.error };
        const { document, owner } = selected;
        const readiness = await owner.lifecycle.client.sendRequest("pysx/readiness",
          { uri: document.uri.toString() });
        return { document, folder: owner.folder, python: owner.python, readiness };
      },
  };
  const controller = setup.register(context, { output, adapter: setupAdapter });
  const offerSetup = () => setup.offer(context,
    { output, adapter: setupAdapter, controller })
    .catch((error) => output.appendLine(`pysx: setup offer failed: ${error.message ?? error}`));

  async function rename(requested) {
    const selected = active();
    if (selected.error) return fail(selected.error);
    const { document } = selected;
    const position = vscode.window.activeTextEditor.selection.active;
    const name = requested ?? await vscode.window.showInputBox({
      prompt: "Rename Python symbol and matching markup",
      validateInput: (value) => /^[A-Za-z_]\w*$/.test(value) ? null : "Enter a Python identifier",
    });
    if (!name) return false;
    const edit = await vscode.commands.executeCommand(
      "vscode.executeDocumentRenameProvider", document.uri, position, name);
    if (!edit || !edit.size) return fail("pysx: That symbol could not be renamed safely.");
    return vscode.workspace.applyEdit(edit);
  }

  async function refresh() {
    const selected = active();
    if (selected.error) return fail(selected.error);
    await selected.owner.lifecycle.client.sendRequest("pysx/refresh",
      { uri: selected.document.uri.toString() });
    return true;
  }

  const traced = (name, action) => (...args) =>
    trace.observe("command", name, {}, () => action(...args));
  context.subscriptions.push(
    vscode.commands.registerCommand("pysx.rename", traced("rename", rename)),
    vscode.commands.registerCommand("pysx.refresh", traced("refresh", refresh)),
    vscode.window.onDidChangeActiveTextEditor((editor) =>
      vscode.commands.executeCommand("setContext", "pysx.managedDocument",
        editor?.document?.languageId === "python" && editor.document.uri.scheme === "file")),
  );
  void vscode.commands.executeCommand("setContext", "pysx.managedDocument",
    vscode.window.activeTextEditor?.document?.languageId === "python");

  return { ...controller, rename, refresh, offerSetup, owned: setup.OWNED };
};
