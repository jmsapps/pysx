import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, rmSync, readFileSync, writeFileSync, realpathSync } from "node:fs";
import { tmpdir, homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { pylanceHost } from "../../tests/pylance_host.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const SUITES = ["diagnostics", "packaging", "authoring"];
const args = process.argv.slice(2);
if (!args.length && process.env.PYSX_EDITOR_PROBE !== "1") {
  for (const registered of SUITES) {
    const result = spawnSync(process.execPath, [fileURLToPath(import.meta.url),
      "--suite", registered], { cwd: root, stdio: "inherit" });
    if (result.status !== 0) process.exit(result.status ?? 1);
  }
  process.exit(0);
}
let suite = "diagnostics";
for (let i = 0; i < args.length; i += 2) {
  if (args[i] !== "--suite" || !args[i + 1]) throw new Error("invalid selectors");
  suite = args[i + 1];
}
if (!SUITES.includes(suite)) throw new Error("empty editor selection");
const host = pylanceHost();
const registryRoot = process.env.PYSX_EXTENSIONS_DIR ?? path.join(homedir(), ".vscode", "extensions");
const registryPath = path.join(registryRoot, "extensions.json");
const pythonEntry = existsSync(registryPath) && JSON.parse(readFileSync(registryPath, "utf8"))
  .filter((item) => item.identifier.id === "ms-python.python")
  .sort((a, b) => b.version.localeCompare(a.version, undefined, { numeric: true }))[0];
const pythonExtension = process.env.PYSX_PYTHON_EXTENSION ?? (pythonEntry && path.join(registryRoot, pythonEntry.relativeLocation));
if (!pythonExtension || !existsSync(pythonExtension)) throw new Error("missing Python extension for actual interpreter/provider qualification");
const ruffEntry = existsSync(registryPath) && JSON.parse(readFileSync(registryPath, "utf8"))
  .filter((item) => item.identifier.id === "charliermarsh.ruff")
  .sort((a, b) => b.version.localeCompare(a.version, undefined, { numeric: true }))[0];
const ruffExtension = process.env.PYSX_RUFF_EXTENSION ?? (ruffEntry && path.join(registryRoot, ruffEntry.relativeLocation));
if (suite === "authoring" && (!ruffExtension || !existsSync(ruffExtension))) throw new Error("missing Ruff extension for template-aware configuration qualification; run npm --prefix editor run setup:host");
const selected = spawnSync("uv", ["run", "--project", root, "python", "-c", "import sys; print(sys.executable)"], { cwd: root, encoding: "utf8", timeout: 30000 });
if (selected.status !== 0 || !selected.stdout.trim()) throw new Error(`interpreter discovery failed: ${selected.stderr}`);
const interpreter = selected.stdout.trim();
const locations = process.platform === "darwin" ? ["/Applications/Visual Studio Code.app/Contents/MacOS/Code"] :
  process.platform === "win32" ? [path.join(process.env.LOCALAPPDATA ?? homedir(), "Programs/Microsoft VS Code/Code.exe")] :
  ["/usr/share/code/code", "/usr/lib/code/code"];
const executable = process.env.PYSX_VSCODE_EXECUTABLE ?? locations.find(existsSync);
if (process.env.PYSX_VSCODE_EXECUTABLE && !existsSync(executable)) throw new Error("missing VSCode executable");
const workspace = realpathSync(mkdtempSync(path.join(tmpdir(), "pysx-editor-")));
let child;
try {
  const proof = suite === "packaging";
  const build = spawnSync("uv", ["run", "--project", root, "python", ...(proof ? ["-c",
    "import json,sys; from pathlib import Path; from tests.prototypes.distribution import prepare_editor_fixture; print(json.dumps(prepare_editor_fixture(Path(sys.argv[1]))))", workspace] : ["editor/build_vsix.py"])],
    { cwd: root, encoding: "utf8", timeout: proof ? 120000 : 30000 });
  if (build.status !== 0) throw new Error(`fresh VSIX build failed: ${build.stderr}`);
  const fixture = proof ? JSON.parse(build.stdout) : null;
  const artifact = fixture ? fixture.vsix : build.stdout.trim();
  const unpack = spawnSync("uv", ["run", "--project", root, "python", "-c", "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])", artifact, workspace], { cwd: root, encoding: "utf8", timeout: 30000 });
  if (unpack.status !== 0) throw new Error(`fresh VSIX extraction failed: ${unpack.stderr}`);
  const extension = path.join(workspace, "extension");
  const consumer = path.join(workspace, "consumer");
  if (suite !== "packaging") mkdirSync(consumer);
  const manifest = JSON.parse(readFileSync(path.join(extension, "package.json"), "utf8"));
  if (process.env.PYSX_EDITOR_PROBE === "1") {
    console.log(JSON.stringify({ root, interpreter, host: host.version, client: manifest.version, workspace }));
  } else {
    // Keep the test launcher in its own process group so a deadline cleans the
    // actual Electron host and extension workers, including failed startups.
    child = spawn(process.execPath, [fileURLToPath(new URL("launch.mjs", import.meta.url))], {
      cwd: root, detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, PYSX_EDITOR_INPUT: JSON.stringify({ executable, extension, workspace,
        root: fixture ? fixture.consumer : consumer, checkout: root, host, suite,
        python: fixture?.python ?? interpreter, pythonExtension, ruffExtension: suite === "authoring" ? ruffExtension : null }) },
    });
    if (process.env.PYSX_EDITOR_STATE_FILE) {
      writeFileSync(process.env.PYSX_EDITOR_STATE_FILE, JSON.stringify({ workspace, pid: child.pid }));
    }
    let output = "";
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`editor deadline exceeded: ${output}`)),
        process.env.PYSX_EDITOR_FORCE_TIMEOUT === "1" ? 500 : process.env.PYSX_EDITOR_PERFORMANCE === "1" ? 240000 : 120000);
      child.stdout.on("data", (data) => { output = (output + data).slice(-30000); });
      child.stderr.on("data", (data) => { output = (output + data).slice(-30000); });
      child.once("error", (error) => { clearTimeout(timer); reject(error); });
      // "close" rather than "exit": the host's final assertion line is still
      // buffered when "exit" fires.
      child.once("close", (code) => { clearTimeout(timer); code === 0 ? resolve() : reject(new Error(`editor host exited ${code}: ${output}`)); });
    });
    const cases = suite === "authoring" ? 10 : 3;
    if (!output.includes(`PYSX EDITOR CASES PASSED: ${cases}`)) throw new Error(`missing host assertions: ${output}`);
    console.log(output.split("\n").filter((line) => line.startsWith("VSCode ") || line.startsWith("PYSX EDITOR CASES") || line.startsWith("PYSX EDITOR PERFORMANCE")).join("\n"));
    console.log(`EDITOR VERIFICATION PASSED: ${suite} (client ${manifest.version}, Pylance ${host.version}, ${cases} cases)`);
  }
} finally {
  if (child?.pid) {
    if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"]);
    else {
      try { process.kill(-child.pid, "SIGKILL"); }
      catch (error) { if (!["ESRCH", "EPERM"].includes(error.code)) throw error; }
    }
  }
  rmSync(workspace, { recursive: true, force: true });
}
