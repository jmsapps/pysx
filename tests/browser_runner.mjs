import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
import playwright from "playwright";

const root = fileURLToPath(new URL("../", import.meta.url));
// Browser bundles carry their own native libraries; GUI/toolchain overrides can
// otherwise make Firefox load an incompatible Homebrew NSS library on macOS.
if (process.platform === "darwin") delete process.env.DYLD_LIBRARY_PATH;
const args = process.argv.slice(2);
let phase = "PYSX-3", stage = "ST-1";
for (let i = 0; i < args.length; i += 2) {
  if (!["--phase", "--stage"].includes(args[i]) || !args[i + 1]) {
    throw new Error("expected --phase PYSX-N --stage ST-S");
  }
  if (args[i] === "--phase") phase = args[i + 1];
  else stage = args[i + 1];
}
if (phase !== "PYSX-3" || stage !== "ST-1") throw new Error("empty browser selection");

const children = new Set();
async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise((resolve) => child.once("exit", resolve));
  const kill = (signal) => {
    if (process.platform === "win32") {
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"]);
    } else {
      try { process.kill(-child.pid, signal); } catch (error) {
        if (error.code !== "ESRCH") throw error;
      }
    }
  };
  kill("SIGTERM");
  const timer = setTimeout(() => kill("SIGKILL"), 3000);
  await exited;
  clearTimeout(timer);
}
async function cleanup() { await Promise.all([...children].map(stop)); }
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => { void cleanup().finally(() => process.exit(1)); });
}
function start(command, argv, environment = {}) {
  const child = spawn(command, argv, {
    cwd: root, detached: process.platform !== "win32",
    env: { ...process.env, ...environment }, stdio: ["ignore", "pipe", "pipe"],
  });
  children.add(child);
  return child;
}
async function wait(child, ready = null, timeout = 30000) {
  let output = "";
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => finish(new Error(`child timeout: ${output}`)), timeout);
    function finish(error) {
      clearTimeout(timer);
      child.off("error", failed); child.off("exit", exited);
      child.stdout.off("data", read); child.stderr.off("data", read);
      if (error) reject(error); else resolve();
    }
    const failed = (error) => finish(error);
    const exited = (code) => finish(code === 0 && !ready ? null : new Error(`child exited ${code}: ${output}`));
    const read = (data) => {
      output = (output + data.toString()).slice(-20000);
      if (ready && output.includes(ready)) finish();
    };
    child.on("error", failed); child.on("exit", exited);
    child.stdout.on("data", read); child.stderr.on("data", read);
  });
  return output;
}
async function freePort(requested = 0) {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject); server.listen(requested, "127.0.0.1", resolve);
  });
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return port;
}
try {
  const python = start("uv", ["run", "--project", ".", "python", "-c", "import sys; print(sys.executable)"]);
  const interpreter = (await wait(python)).trim();
  if (!interpreter) throw new Error("missing selected interpreter");
  for (const engine of ["chromium", "firefox", "webkit"]) {
    // Preflight ensures an absent engine cannot silently reduce coverage.
    const browser = await playwright[engine].launch(); await browser.close();
  }
  let total = 0;
  for (const [index, name, script, banner] of [
    [0, "counter", "browser.mjs", "BROWSER ACCEPTANCE PASSED"],
    [1, "todos", "browser_todos.mjs", "TODOS BROWSER ACCEPTANCE PASSED"],
  ]) {
    const port = await freePort(process.env.PYSX_BROWSER_PORT ? Number(process.env.PYSX_BROWSER_PORT) + index : 0);
    const server = start(interpreter, ["run_example.py", "run", name, "--port", String(port)]);
    await wait(server, "pysx ready", 15000);
    for (const engine of ["chromium", "firefox", "webkit"]) {
      const suite = start(process.execPath, [`tests/${script}`, String(port)], { PYSX_BROWSER_ENGINE: engine });
      const output = await wait(suite, null, 60000);
      if (!output.includes(banner)) throw new Error(`missing suite banner: ${output}`);
      if (process.env.PYSX_BROWSER_FORCE_FAILURE === "1") throw new Error("forced assertion failure");
      const count = output.split("\n").filter((line) => line.startsWith("  ok  ")).length;
      if (!count) throw new Error("empty assertions");
      total += count;
      console.log(`${engine}: ${name}, 1 case, ${count} assertions`);
    }
    await stop(server);
  }
  await cleanup();
  console.log(`BROWSER VERIFICATION PASSED: ${phase}/${stage} (6 cases, ${total} assertions)`);
} catch (error) {
  console.error(error);
  await cleanup();
  process.exitCode = 1;
}
