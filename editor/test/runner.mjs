import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir, homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { pylanceHost } from "../../tests/pylance_host.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const args = process.argv.slice(2);
let phase = "PYSX-3", stage = "ST-3";
for (let i = 0; i < args.length; i += 2) {
  if (!["--phase", "--stage"].includes(args[i]) || !args[i + 1]) throw new Error("invalid selectors");
  if (args[i] === "--phase") phase = args[i + 1]; else stage = args[i + 1];
}
if (phase !== "PYSX-3" || stage !== "ST-3") throw new Error("empty editor selection");
const host = pylanceHost();
const selected = spawnSync("uv", ["run", "--project", root, "python", "-c", "import sys; print(sys.executable)"], { cwd: root, encoding: "utf8", timeout: 30000 });
if (selected.status !== 0 || !selected.stdout.trim()) throw new Error(`interpreter discovery failed: ${selected.stderr}`);
const interpreter = selected.stdout.trim();
const locations = process.platform === "darwin" ? ["/Applications/Visual Studio Code.app/Contents/MacOS/Code"] :
  process.platform === "win32" ? [path.join(process.env.LOCALAPPDATA ?? homedir(), "Programs/Microsoft VS Code/Code.exe")] :
  ["/usr/share/code/code", "/usr/lib/code/code"];
const executable = process.env.PYSX_VSCODE_EXECUTABLE ?? locations.find(existsSync);
if (process.env.PYSX_VSCODE_EXECUTABLE && !existsSync(executable)) throw new Error("missing VSCode executable");
const workspace = mkdtempSync(path.join(tmpdir(), "pysx-editor-"));
const build = spawnSync("uv", ["run", "--project", root, "python", "editor/build_vsix.py"], { cwd: root, encoding: "utf8", timeout: 30000 });
let child;
try {
  if (build.status !== 0) throw new Error(`fresh VSIX build failed: ${build.stderr}`);
  const artifact = build.stdout.trim();
  const unpack = spawnSync("uv", ["run", "--project", root, "python", "-c", "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])", artifact, workspace], { cwd: root, encoding: "utf8", timeout: 30000 });
  if (unpack.status !== 0) throw new Error(`fresh VSIX extraction failed: ${unpack.stderr}`);
  const extension = path.join(workspace, "extension");
  const manifest = JSON.parse(readFileSync(path.join(extension, "package.json"), "utf8"));
  if (process.env.PYSX_EDITOR_PROBE === "1") {
    console.log(JSON.stringify({ root, interpreter, host: host.version, client: manifest.version, workspace }));
  } else {
    // Keep the test launcher in its own process group so a deadline cleans the
    // actual Electron host and extension workers, including failed startups.
    child = spawn(process.execPath, [fileURLToPath(new URL("launch.mjs", import.meta.url))], {
      cwd: root, detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, PYSX_EDITOR_INPUT: JSON.stringify({ executable, extension, workspace, root, host, phase, stage }) },
    });
    if (process.env.PYSX_EDITOR_STATE_FILE) {
      writeFileSync(process.env.PYSX_EDITOR_STATE_FILE, JSON.stringify({ workspace, pid: child.pid }));
    }
    let output = "";
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`editor deadline exceeded: ${output}`)),
        process.env.PYSX_EDITOR_FORCE_TIMEOUT === "1" ? 500 : 120000);
      child.stdout.on("data", (data) => { output = (output + data).slice(-30000); });
      child.stderr.on("data", (data) => { output = (output + data).slice(-30000); });
      child.once("error", (error) => { clearTimeout(timer); reject(error); });
      child.once("exit", (code) => { clearTimeout(timer); code === 0 ? resolve() : reject(new Error(`editor host exited ${code}: ${output}`)); });
    });
    if (!output.includes("PYSX EDITOR CASES PASSED: 3")) throw new Error(`missing host assertions: ${output}`);
    console.log(output.split("\n").filter((line) => line.startsWith("VSCode ") || line.startsWith("PYSX EDITOR CASES")).join("\n"));
    console.log(`EDITOR VERIFICATION PASSED: ${phase}/${stage} (client ${manifest.version}, Pylance ${host.version}, 3 cases)`);
  }
} finally {
  if (child?.pid) {
    if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"]);
    else {
      try { process.kill(-child.pid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") throw error; }
    }
  }
  rmSync(workspace, { recursive: true, force: true });
}
