import { runTests } from "@vscode/test-electron";
import { fileURLToPath } from "node:url";
import path from "node:path";

const input = JSON.parse(process.env.PYSX_EDITOR_INPUT);
// A launcher invoked from VSCode must not become another Node extension host.
for (const name of Object.keys(process.env)) {
  if (name.startsWith("VSCODE_") || name === "ELECTRON_RUN_AS_NODE" || name === "NODE_OPTIONS" || name === "DYLD_LIBRARY_PATH") delete process.env[name];
}
if (process.env.PYSX_EDITOR_FORCE_TIMEOUT === "1") {
  await new Promise((resolve) => setTimeout(resolve, 60000));
}
await runTests({
  version: "1.140.0",
  vscodeExecutablePath: input.executable,
  extensionDevelopmentPath: [input.extension, input.host.directory],
  extensionTestsPath: fileURLToPath(new URL("suite.cjs", import.meta.url)),
  extensionTestsEnv: { PYSX_EDITOR_WORKSPACE: input.workspace },
  launchArgs: [input.root, "--user-data-dir", path.join(input.workspace, "profile"),
    "--extensions-dir", path.join(input.workspace, "extensions"),
    "--skip-welcome", "--skip-release-notes", "--disable-workspace-trust", "--no-sandbox"],
});
