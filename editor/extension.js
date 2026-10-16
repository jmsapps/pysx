// One dispatcher, two clients. The worker integration stays the default until the
// resident language server has the same qualified feature coverage.
const vscode = require("vscode");
const path = require("node:path");
const fs = require("node:fs");

let active = null;

async function guardAdvertised(context, mode) {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(context.extensionPath, "package.json"), "utf8"));
  const advertised = manifest.contributes.commands.map((item) => item.command);
  const registered = new Set(await vscode.commands.getCommands(true));
  const other = mode === "resident" ? "worker integration" : "resident language server";

  // An advertised command that the active mode does not implement must say so
  // rather than fail with "command not found".
  for (const command of advertised.filter((item) => !registered.has(item))) {
    context.subscriptions.push(vscode.commands.registerCommand(command, () => {
      const message = `pysx: "${command}" is provided by the ${other}. ` +
        "Change `pysx.useLanguageServer` and reload to use it.";
      void vscode.window.showWarningMessage(message);
      return false;
    }));
  }
}

exports.activate = async function (context) {
  const resident = vscode.workspace.getConfiguration("pysx").get("useLanguageServer") === true;
  active = resident ? require("./client.js") : require("./authoring.js");

  const api = await active.activate(context);
  await guardAdvertised(context, resident ? "resident" : "worker");

  return api;
};

exports.deactivate = async function () {
  const previous = active;
  active = null;
  await previous?.deactivate?.();
};
