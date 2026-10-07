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
// The Marketplace answers a cold runner with 503 often enough to redden a build
// that has nothing wrong with it. Retry bounded, then fail: a sustained outage
// is a blocker, never a skipped pass.
const attempts = Number(process.env.PYSX_HOST_INSTALL_ATTEMPTS ?? 4);
let failure = null;
let result = null;
for (let attempt = 1; attempt <= attempts; attempt += 1) {
  try {
    result = await runVSCodeCommand([
      "--install-extension", "ms-python.vscode-pylance@2026.4.1", "--force",
      "--install-extension", "ms-python.python@2026.8.0",
      "--install-extension", "charliermarsh.ruff@2026.84.0",
    ], { version: "1.140.0", spawn: { timeout: 120000 } });
    failure = null;
    break;
  } catch (error) {
    failure = error;
    if (attempt === attempts) break;
    const delay = 5000 * 2 ** (attempt - 1);
    console.log(`- Install attempt ${attempt}/${attempts} failed (exit ${error.exitCode ?? "?"}); retrying in ${delay / 1000}s`);
    await new Promise(resolve => setTimeout(resolve, delay));
  }
}
if (failure) {
  console.error(`Pylance install failed after ${attempts} attempts; the extension host is unavailable.`);
  throw failure;
}
process.env.PYSX_EXTENSIONS_DIR = extensions;
const host = pylanceHost();
console.log(result.stdout.trim());
console.log(`Managed Pylance ${host.version}; sha256 ${host.sha256}`);
console.log(`Set PYSX_EXTENSIONS_DIR=${extensions} for grammar/pytest/editor runs.`);
