import {spawn, spawnSync} from "node:child_process";
import {dirname, join} from "node:path";

const interpreters = new Map();

function selectedPython(repository) {
  if (!interpreters.has(repository)) {
    const selected = spawnSync("uv", ["run", "--project", repository, "python", "-c",
      "import sys; print(sys.executable)"], {cwd: repository, encoding: "utf8", timeout: 30000});
    if (selected.status !== 0) throw new Error(`project interpreter lookup failed: ${selected.stderr || selected.error}`);
    interpreters.set(repository, selected.stdout.trim());
  }
  return interpreters.get(repository);
}

export function spawnCli(repository, args, options) {
  const python = selectedPython(repository);
  if (process.platform !== "win32") {
    return spawn(join(dirname(python), "pysx"), args, options);
  }
  const child = spawn(python, [join(repository, "tests", "cli_control_fixture.py"), ...args],
    {...options, detached: false, stdio: ["pipe", "pipe", "pipe"]});
  child.stdin.on("error", error => {
    if (!["EPIPE", "ERR_STREAM_DESTROYED"].includes(error.code)) child.emit("error", error);
  });
  return child;
}

export function signalCli(child, signal) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === "win32") child.stdin.write(`${signal}\n`);
  else {
    try { process.kill(-child.pid, signal); }
    catch (error) { if (error.code !== "ESRCH") throw error; }
  }
}
