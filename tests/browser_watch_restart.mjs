import assert from "node:assert/strict";
import {once} from "node:events";
import {cp, mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {createServer} from "node:net";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import playwright from "playwright";
import {spawnCli, signalCli} from "./cli_process.mjs";

const repository = fileURLToPath(new URL("../", import.meta.url));
const directory = await mkdtemp(join(tmpdir(), "pysx-watch-restart-"));
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const listener = createServer();
listener.listen(0, "127.0.0.1");
await once(listener, "listening");
const port = listener.address().port;
await new Promise(resolve => listener.close(resolve));
const base = `http://127.0.0.1:${port}`;
let running = null;
let output = "";

async function start() {
  const child = spawnCli(repository, ["--app",
    "examples.durable_state:app", "--watch", "--port", String(port)], {
    cwd: directory, detached: process.platform !== "win32",
    env: {...process.env, PYSX_STATE_PATH:join(directory, "state.sqlite3")},
    stdio:["ignore", "pipe", "pipe"],
  });
  running = {child, ended:once(child, "exit")};
  const drain = data => { output = (output + data).slice(-20000); };
  child.stdout.on("data", drain); child.stderr.on("data", drain);
  for (let attempt = 0; attempt < 200; attempt++) {
    assert.equal(child.exitCode, null, output);
    try { if ((await fetch(base)).ok) return; } catch {}
    await pause(50);
  }
  throw new Error(`watch server not ready: ${output}`);
}

async function stop() {
  if (!running) return;
  const owned = running; running = null;
  if (owned.child.exitCode === null) signalCli(owned.child, "SIGINT");
  const timer = setTimeout(() => {
    try { signalCli(owned.child, "SIGKILL"); } catch {}
  }, 10000);
  try {
    const [code, signal] = await owned.ended;
    assert.equal(code, 0,
      `watch CLI exited code=${code} signal=${signal} platform=${process.platform}\n${output}`);
  } finally { clearTimeout(timer); }
}

try {
  await cp(join(repository, "examples"), join(directory, "examples"), {
    recursive:true, filter:source => !source.includes("__pycache__"),
  });
  const source = join(directory, "examples", "durable_state.py");
  // Expose the real startup-zero window to polling in every engine.
  let content = (await readFile(source, "utf8")).replace("import asyncio\n",
    "import asyncio\nimport time\ntime.sleep(0.8)\n");
  await writeFile(source, content);
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  let stage = "initial-startup";
  page.on("pageerror", error => {
    errors.push(error.message);
    console.error(`WATCH RESTART PAGE ERROR: ${JSON.stringify({
      name: error.name, message: error.message, stack: error.stack,
      url: page.url(), supervisorRunning: running?.child.exitCode === null,
      stage,
    })}`);
  });
  let failedStatusRequests = 0;
  page.on("requestfailed", request => {
    if (new URL(request.url()).pathname !== "/__pysx_dev__/status" || failedStatusRequests++ >= 10) return;
    console.error(`WATCH RESTART STATUS FAILURE: ${JSON.stringify({
      url: request.url(), failure: request.failure(),
      supervisorRunning: running?.child.exitCode === null,
      stage,
    })}`);
  });
  const status = async () => (await fetch(base + "/__pysx_dev__/status")).json();
  const connected = target => target.waitForFunction(() =>
    document.getElementById("pysx-root")?.dataset.pysxConnection === "connected");
  const count = (target, value) => target.waitForFunction(value =>
    document.getElementById("durable-count").textContent === String(value), value);
  const startup = [];
  page.on("response", async response => {
    if (!response.url().includes("/__pysx_dev__/status")) return;
    try { startup.push(await response.json()); } catch {}
  });
  await start(); await page.goto(base); await connected(page);
  await page.locator("#durable-increment").click(); await count(page, 1);
  await page.locator("#durable-draft").fill("saved before restart");
  await page.waitForFunction(() => document.getElementById("durable-echo").textContent === "saved before restart");

  for (let cycle = 0; cycle < 2; cycle++) {
    stage = `restart-${cycle}-stop`;
    const before = await status();
    const setup = await page.locator("#durable-generation").textContent();
    await page.evaluate(() => { window.watchRestartMarker = "original document"; });
    startup.length = 0;
    await stop();
    // Let the closed notification socket enter its ordinary polling fallback.
    await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection !== "connected");
    stage = `restart-${cycle}-start`;
    await start();
    await page.waitForFunction(setup =>
      document.getElementById("pysx-root").dataset.pysxConnection === "connected"
      && document.getElementById("durable-generation").textContent !== setup, setup);
    assert.equal(await page.evaluate(() => window.watchRestartMarker), "original document");
    await count(page, cycle + 1);
    assert.equal(await page.locator("#durable-draft").inputValue(), "saved before restart");
    const after = await status();
    assert.equal(after.generation, 1);
    assert.ok(after.instance && before.instance && after.instance !== before.instance);
    assert.ok(startup.some(value => value.generation === 0), "startup-zero status was not observed");
    if (cycle === 0) {
      stage = `restart-${cycle}-edit`;
      content = content.replace('Title: "Durable state"', 'Title: "Edited durable state"');
      await writeFile(source, content);
      try {
        await page.getByRole("heading", {name:"Edited durable state", exact:true}).waitFor();
      } catch (cause) {
        const supervisor = await fetch(base + "/__pysx_dev__/status", {signal: AbortSignal.timeout(3000)})
          .then(response => response.json()).catch(error => ({error: error.message}));
        throw new Error(`watch edit not adopted: ${JSON.stringify({stage, supervisor, output})}`, {cause});
      }
      await connected(page); await count(page, 1);
      assert.equal((await status()).instance, after.instance);
      assert.ok((await status()).generation > 1);
      await page.locator("#durable-increment").click(); await count(page, 2);
    }
  }
  console.log("  ok  actual watch CLI Ctrl+C/restart recovers count and draft without navigating, including startup-zero and advanced old generations");

  const other = await context.newPage();
  stage = "fresh-tab-and-refresh";
  await other.goto(base); await connected(other); await count(other, 0);
  await other.reload(); await connected(other); await count(other, 0);
  await count(page, 2);
  assert.equal(await page.locator("#durable-draft").inputValue(), "saved before restart");
  console.log("  ok  refresh and same-cookie new tabs start independent sessions after recovery");

  const beforeFresh = await status();
  stage = "incompatible-identity-reload";
  content = content.replace('application="durable-example"', 'application="different-example"');
  await writeFile(source, content);
  await count(page, 0); await connected(page);
  await page.waitForFunction(() => window.watchRestartMarker === undefined);
  await connected(page);
  const afterFresh = await status();
  assert.equal(afterFresh.instance, beforeFresh.instance);
  assert.equal(afterFresh.fresh_generation, afterFresh.generation);
  assert.ok(afterFresh.generation > beforeFresh.generation);
  assert.equal(await page.locator("#durable-draft").inputValue(), "My saved draft");
  assert.deepEqual(errors, []);
  console.log("  ok  intentional incompatible identity changes in the same supervisor still reload and reset");
  await context.close();
  console.log("WATCH RESTART BROWSER PASSED");
} finally {
  try { await stop(); } finally {
    await browser.close(); await rm(directory, {recursive:true, force:true});
  }
}
