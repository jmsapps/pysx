import { spawn, spawnSync } from "node:child_process";
import { appendFileSync, existsSync, mkdtempSync, mkdirSync, rmSync, readFileSync, writeFileSync, realpathSync, readdirSync } from "node:fs";
import { tmpdir, homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { pylanceHost } from "../../tests/pylance_host.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const SUITES = ["diagnostics", "packaging", "authoring", "client_verify", "editor_surface", "template_import_usage", "interpreter_routing", "save_latency", "validate_integrated"];
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
if (suite === "client_verify") {
  const result = spawnSync(process.execPath, ["--test", "editor/test/highlighting.test.cjs"],
    { cwd: root, stdio: "inherit" });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
if (["client_verify", "editor_surface"].includes(suite) && !process.env.PYSX_EDITOR_ACTIVATION_ORDER) {
  for (const order of ["client-first", "pylance-first"]) {
    const result = spawnSync(process.execPath, [fileURLToPath(import.meta.url), "--suite", suite],
      { cwd: root, stdio: "inherit", env: { ...process.env, PYSX_EDITOR_ACTIVATION_ORDER: order } });
    if (result.status !== 0) process.exit(result.status ?? 1);
  }
  process.exit(0);
}
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
if (["authoring", "template_import_usage", "editor_surface"].includes(suite) && (!ruffExtension || !existsSync(ruffExtension))) throw new Error("missing Ruff extension for template-aware configuration qualification; run npm --prefix editor run setup:host");
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
let failed = false;
const outputFile = path.join(workspace, "host-output.log");
const lifecycleFailures = /Client is stopping but no stop promise|rejected promise not handled|unhandled rejection|Handling connection close failed|Restarting server failed|Stopping server failed|Stopping the server timed out/i;
const inspect = (directory, failures = lifecycleFailures) => {
  const findings = [];
  if (!existsSync(directory)) return findings;
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) findings.push(...inspect(filename, failures));
    else if (entry.name.endsWith(".log")) {
      const lines = readFileSync(filename, "utf8").split("\n");
      for (let index = 0; index < lines.length; index++) {
        if (failures.test(lines[index])) {
          findings.push(`${path.relative(workspace, filename)}:${index + 1}\n` +
            lines.slice(Math.max(0, index - 2), index + 13).join("\n"));
        }
      }
    }
  }
  return findings;
};
try {
  const proof = suite === "packaging";
  const build = spawnSync("uv", ["run", "--project", root, "python", ...(proof ? ["-c",
    "import json,sys; from pathlib import Path; from tests.prototypes.distribution import prepare_editor_fixture; print(json.dumps(prepare_editor_fixture(Path(sys.argv[1]))))", workspace] : ["editor/build_vsix.py"])],
    { cwd: root, encoding: "utf8", timeout: proof ? 120000 : 30000 });
  if (build.status !== 0) throw new Error(`fresh VSIX build failed: ${build.stderr}`);
  let fixture = proof ? JSON.parse(build.stdout) : null;
  const artifact = fixture ? fixture.vsix : build.stdout.trim();
  if (suite === "validate_integrated") {
    const prepared = spawnSync("uv", ["run", "--project", root, "python", "-c",
      "import json,sys; from pathlib import Path; sys.path.insert(0, 'tests'); from test_field_application import prepare_editor; print(json.dumps(prepare_editor(Path(sys.argv[1]))))", workspace],
      {cwd: root, encoding: "utf8", timeout: 120000});
    if (prepared.status !== 0) throw new Error(`installed course preparation failed: ${prepared.stderr}`);
    fixture = JSON.parse(prepared.stdout);
  }
  const unpack = spawnSync("uv", ["run", "--project", root, "python", "-c", "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])", artifact, workspace], { cwd: root, encoding: "utf8", timeout: 30000 });
  if (unpack.status !== 0) throw new Error(`fresh VSIX extraction failed: ${unpack.stderr}`);
  const extension = path.join(workspace, "extension");
  const consumer = path.join(workspace, "consumer");
  if (!fixture) mkdirSync(consumer);
  // Multi-root qualification needs real sibling and nested roots, so the host
  // opens a generated workspace file rather than a single folder.
  let multiRoot = null;
  let alternate = null;
  let bare = null;
  if (suite === "interpreter_routing") {
    const secondary = path.join(workspace, "secondary");
    const nested = path.join(consumer, "nested");
    mkdirSync(secondary);
    mkdirSync(nested);
    multiRoot = path.join(workspace, "pysx.code-workspace");
    writeFileSync(multiRoot, JSON.stringify({ folders: [{ path: consumer }, { path: secondary }, { path: nested }], settings: {} }));
    // A second executable identity for the same working environment, so a
    // folder's selection is observable as an actual different child command.
    alternate = path.join(workspace, "alternate-python");
    const startupDelay = Number(process.env.PYSX_EDITOR_ROUTING_START_DELAY_MS ?? 0);
    if (!Number.isFinite(startupDelay) || startupDelay < 0 || startupDelay > 30000) {
      throw new Error("routing startup delay must be between 0 and 30000 ms");
    }
    // Fault injection affects only actual language-server starts, leaving
    // environment discovery and the missing-extra checks intact.
    const delay = startupDelay ?
      `if [ "$1" = "-m" ] && [ "$2" = "pysx.tooling.lsp.lsp" ]; then sleep ${startupDelay / 1000}; fi\n` : "";
    writeFileSync(alternate, `#!/bin/sh\n${delay}exec ${JSON.stringify(interpreter)} "$@"\n`, { mode: 0o755 });
    bare = path.join(workspace, "bare");
    const created = spawnSync("uv", ["venv", "--python", interpreter, bare], { cwd: root, encoding: "utf8", timeout: 120000 });
    if (created.status !== 0) throw new Error(`bare environment creation failed: ${created.stderr}`);
    bare = path.join(bare, "bin", "python");
    if (!existsSync(bare)) throw new Error("bare environment has no interpreter");
  }
  const manifest = JSON.parse(readFileSync(path.join(extension, "package.json"), "utf8"));
  if (process.env.PYSX_EDITOR_PROBE === "1") {
    console.log(JSON.stringify({ root, interpreter, host: host.version, client: manifest.version, workspace }));
  } else {
    // Keep the test launcher in its own process group so a deadline cleans the
    // actual Electron host and extension workers, including failed startups.
    child = spawn(process.execPath, [fileURLToPath(new URL("launch.mjs", import.meta.url))], {
      cwd: root, detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, PYSX_EDITOR_INPUT: JSON.stringify({ executable, extension, workspace,
        root: multiRoot ?? (fixture ? fixture.consumer : consumer), checkout: root, host, suite,
        python: fixture?.python ?? interpreter, alternate, bare, pythonExtension, ruffExtension: ["authoring", "template_import_usage", "editor_surface"].includes(suite) ? ruffExtension : null }) },
    });
    writeFileSync(outputFile, "");
    if (process.env.PYSX_EDITOR_STATE_FILE) {
      writeFileSync(process.env.PYSX_EDITOR_STATE_FILE, JSON.stringify({ workspace, pid: child.pid }));
    }
    let output = "";
    await new Promise((resolve, reject) => {
      // Routing creates three clients and replaces them repeatedly. Its shared
      // host budget must allow healthy slow starts across all eight cases;
      // the suite separately bounds each stage and reports its progress.
      const timeout = process.env.PYSX_EDITOR_FORCE_TIMEOUT === "1" ? 500 :
        suite === "interpreter_routing" || process.env.PYSX_EDITOR_PERFORMANCE === "1" ? 240000 : 120000;
      const timer = setTimeout(() => reject(new Error(`editor deadline exceeded: ${output}`)),
        timeout);
      const capture = (data) => { appendFileSync(outputFile, data); output += data; };
      child.stdout.on("data", capture);
      child.stderr.on("data", capture);
      child.once("error", (error) => { clearTimeout(timer); reject(error); });
      // "close" rather than "exit": the host's final assertion line is still
      // buffered when "exit" fires.
      child.once("close", (code) => { clearTimeout(timer); code === 0 ? resolve() : reject(new Error(`editor host exited ${code}: ${output}`)); });
    });
    const cases = suite === "save_latency" ? 6 : suite === "interpreter_routing" ? 8 : suite === "authoring" ? 11 : suite === "client_verify" ? 12 : suite === "editor_surface" ? 11 : suite === "template_import_usage" ? 7 : 3;
    if (lifecycleFailures.test(output)) throw new Error(`lifecycle failure in host output: ${output}`);
    const findings = inspect(path.join(workspace, "profile", "logs"));
    if (findings.length) throw new Error(`lifecycle failure in host logs:\n${findings.join("\n")}`);
    if (!output.includes(`PYSX EDITOR CASES PASSED: ${cases}`)) throw new Error(`missing host assertions: ${output}`);
    console.log(output.split("\n").filter((line) => line.startsWith("VSCode ") || line.startsWith("PYSX EDITOR CASES") || line.startsWith("PYSX EDITOR SAVE:") || line.startsWith("PYSX EDITOR PERFORMANCE") || line.startsWith("PYSX EDITOR ROUTING:")).join("\n"));
    console.log(`EDITOR VERIFICATION PASSED: ${suite} (client ${manifest.version}, Pylance ${host.version}, ${cases} cases)`);
  }
} catch (error) {
  failed = true;
  console.error(`EDITOR FAILURE: ${suite} (${process.env.PYSX_EDITOR_ACTIVATION_ORDER ?? "default"}); workspace retained: ${workspace}; output: ${outputFile}`);
  try {
    const findings = inspect(path.join(workspace, "profile", "logs"),
      new RegExp(`${lifecycleFailures.source}|timeout waiting for|AssertionError|\\[error\\]`, "i"));
    if (findings.length) console.error(`EDITOR HOST LOG FINDINGS:\n${findings.join("\n")}`);
  } catch (inspectionError) {
    console.error(`editor log inspection failed: ${inspectionError.stack ?? inspectionError}`);
  }
  throw error;
} finally {
  if (child?.pid) {
    if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"]);
    else {
      try { process.kill(-child.pid, "SIGKILL"); }
      catch (error) { if (!["ESRCH", "EPERM"].includes(error.code)) throw error; }
    }
  }
  if (!failed) rmSync(workspace, { recursive: true, force: true });
}
