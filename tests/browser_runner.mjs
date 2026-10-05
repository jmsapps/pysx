import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
import playwright from "playwright";

const root = fileURLToPath(new URL("../", import.meta.url));
// Browser bundles carry their own native libraries; GUI/toolchain overrides can
// otherwise make Firefox load an incompatible Homebrew NSS library on macOS.
if (process.platform === "darwin") delete process.env.DYLD_LIBRARY_PATH;
const args = process.argv.slice(2);
let suite = null;
for (let i = 0; i < args.length; i += 2) {
  if (args[i] !== "--suite" || !args[i + 1]) {
    throw new Error("expected --suite NAME");
  }
  suite = args[i + 1];
}
const registry = [
  { suite: "forms_example", cases: [
    [0, "forms", "browser_forms.mjs", "FORMS BROWSER PASSED"],
  ] },
  { suite: "bindings_form", cases: [
    [0, "forms_fixture", "browser_forms.mjs", "FORMS BROWSER PASSED"],
  ] },
  { suite: "serialization_live", cases: [
    [0, "native_fixture", "browser_serialization.mjs", "SERIALIZATION BROWSER PASSED"],
  ] },
  { suite: "examples", cases: [
    [0, "counter", "browser.mjs", "BROWSER ACCEPTANCE PASSED"],
    [1, "todos", "browser_todos.mjs", "TODOS BROWSER ACCEPTANCE PASSED"],
  ] },
  { suite: "cascade", cases: [
    [0, null, "browser_architecture.mjs", "ARCHITECTURE CASCADE PASSED"],
  ] },
  { suite: "adoption", cases: [
    [0, "adoption", "browser_adoption.mjs", "ARCHITECTURE ADOPTION PASSED"],
  ] },
  { suite: "template_ergonomics", cases: [
    [0, "reactive_state", "browser_operators.mjs", "OPERATORS BROWSER PASSED"],
  ] },
];
const selections = registry.filter((entry) => !suite || entry.suite === suite);
if (!selections.length) throw new Error("empty browser selection");

const children = new Set();
async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise((resolve) => child.once("exit", resolve));
  const kill = (signal) => {
    if (process.platform === "win32") {
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"]);
    } else {
      try { process.kill(-child.pid, signal); } catch (error) {
        if (!["ESRCH", "EPERM"].includes(error.code)) throw error;
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
      child.off("error", failed); child.off("close", closed);
      child.stdout.off("data", read); child.stderr.off("data", read);
      if (error) reject(error); else resolve();
    }
    const failed = (error) => finish(error);
    // "close" rather than "exit": the child's final stdout line is still
    // buffered when "exit" fires, and finish() detaches the data handlers.
    const closed = (code) => finish(code === 0 && !ready ? null : new Error(`child exited ${code}: ${output}`));
    const read = (data) => {
      output = (output + data.toString()).slice(-20000);
      if (ready && output.includes(ready)) finish();
    };
    child.on("error", failed); child.on("close", closed);
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
  let caseTotal = 0;
  for (const selection of selections) {
  const cases = selection.cases;
  let selectedAssertions = 0;
  for (const [index, name, script, banner] of cases) {
    const port = name ? await freePort(process.env.PYSX_BROWSER_PORT ? Number(process.env.PYSX_BROWSER_PORT) + index : 0) : 0;
    const server = name ? start(interpreter, name === "forms_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_forms_fixture:app", "--port", String(port)] :
      name === "native_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_fixture:app", "--port", String(port)] :
      name === "adoption" ?
      ["-m", "tests.prototypes.standalone_host", "--port", String(port)] :
      ["run_example.py", "run", name, "--port", String(port)]) : null;
    if (server) await wait(server, "pysx ready", 15000);
    for (const engine of ["chromium", "firefox", "webkit"]) {
      const suite = start(process.execPath, [`tests/${script}`, String(port)], { PYSX_BROWSER_ENGINE: engine });
      const output = await wait(suite, null, 60000);
      if (!output.includes(banner)) throw new Error(`missing suite banner: ${output}`);
      if (process.env.PYSX_BROWSER_FORCE_FAILURE === "1") throw new Error("forced assertion failure");
      const count = output.split("\n").filter((line) => line.startsWith("  ok  ")).length;
      if (!count) throw new Error("empty assertions");
      total += count;
      selectedAssertions += count;
      console.log(`${engine}: ${name ?? "cascade"}, 1 case, ${count} assertions`);
    }
    if (server) await stop(server);
  }
  await cleanup();
  caseTotal += cases.length * 3;
  console.log(`BROWSER VERIFICATION PASSED: ${selection.suite} (${cases.length * 3} cases, ${selectedAssertions} assertions)`);
  }
  if (selections.length > 1) console.log(`BROWSER VERIFICATION PASSED: all selected (${caseTotal} cases, ${total} assertions)`);
} catch (error) {
  console.error(error);
  await cleanup();
  process.exitCode = 1;
}
