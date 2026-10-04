import { fileURLToPath } from "node:url";
import path from "node:path";
import { pylanceHost } from "../../tests/pylance_host.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
process.chdir(root);
// The tooling computes its cache directory at import time; npm --prefix starts
// in editor/, so load it only after selecting the repository root.
const { runVSCodeCommand } = await import("@vscode/test-electron");
for (const name of Object.keys(process.env)) {
  if (name.startsWith("VSCODE_") || name === "ELECTRON_RUN_AS_NODE" || name === "NODE_OPTIONS") delete process.env[name];
}
const extensions = path.join(root, ".vscode-test", "extensions");
const result = await runVSCodeCommand([
  "--install-extension", "ms-python.vscode-pylance@2026.4.1", "--force",
], { version: "1.140.0", spawn: { timeout: 120000 } });
process.env.PYSX_EXTENSIONS_DIR = extensions;
const host = pylanceHost();
console.log(result.stdout.trim());
console.log(`Managed Pylance ${host.version}; sha256 ${host.sha256}`);
console.log(`Set PYSX_EXTENSIONS_DIR=${extensions} for grammar/pytest/editor runs.`);
