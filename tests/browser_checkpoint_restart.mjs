import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {randomBytes} from "node:crypto";
import {once} from "node:events";
import {mkdtemp, mkdir, readFile, writeFile, rm, realpath} from "node:fs/promises";
import {createServer} from "node:net";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import playwright from "playwright";

const repository = fileURLToPath(new URL("../", import.meta.url));
const directory = await realpath(await mkdtemp(join(tmpdir(), "pysx-browser-checkpoint-")));
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const listener = createServer();
listener.listen(0, "127.0.0.1");
await once(listener, "listening");
const port = listener.address().port;
await new Promise(resolve => listener.close(resolve));
const base = `http://127.0.0.1:${port}`;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let running = null;
let output = "";

async function start(root) {
  const child = spawn("uv", ["run", "--project", repository, "pysx", "--app",
    "checkpoint_app:host", "--port", String(port)], {
    cwd: root, detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"],
    env: {...process.env, PYSX_TEST_KEY_DIRECTORY: join(directory, "keys")},
  });
  running = {child, ended: once(child, "exit")};
  const drain = data => { output = (output + data).slice(-20000); };
  child.stdout.on("data", drain); child.stderr.on("data", drain);
  for (let attempt = 0; attempt < 150; attempt++) {
    assert.equal(child.exitCode, null, output);
    try { if ((await fetch(base)).ok) return; } catch {}
    await pause(30);
  }
  throw new Error(`checkpoint server not ready: ${output}`);
}

async function stop(signal = "SIGINT") {
  if (!running) return;
  const owned = running; running = null;
  if (owned.child.exitCode !== null) { await owned.ended; return; }
  const kill = value => {
    if (process.platform === "win32") owned.child.kill(value);
    else process.kill(-owned.child.pid, value);
  };
  if (owned.child.exitCode === null) kill(signal);
  const timer = setTimeout(() => { try { kill("SIGKILL"); } catch {} }, 5000);
  try {
    const [code, endedSignal] = await owned.ended;
    if (signal === "SIGINT") assert.equal(code, 0, output);
    else assert.equal(endedSignal, "SIGKILL", output);
  } finally { clearTimeout(timer); }
}

try {
  const firstRoot = join(directory, "first");
  const secondRoot = join(directory, "second");
  await Promise.all([firstRoot, secondRoot, join(directory, "keys")].map(path => mkdir(path, {mode: 0o700})));
  await writeFile(join(directory, "keys", "recovery.keys"),
    Buffer.concat([Buffer.from("PSK1"), randomBytes(8), randomBytes(32)]), {mode: 0o600});
  const fixture = await readFile(join(repository, "tests", "browser_checkpoint_fixture.py"), "utf8");
  const host = '\nfrom pathlib import Path\nimport os\nfrom pysx import Application, HostConfig\n' +
    'host = Application(app, config=HostConfig(recovery_namespace="checkpoint-browser", ' +
    'recovery_key_directory=Path(os.environ["PYSX_TEST_KEY_DIRECTORY"])))\n';
  await writeFile(join(firstRoot, "checkpoint_app.py"), fixture + host);
  await writeFile(join(secondRoot, "checkpoint_app.py"), "# relocated compatible deployment\n" + fixture + host);
  await start(firstRoot);
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    const Original = WebSocket;
    window.WebSocket = class extends Original {
      constructor(...args) {
        super(...args);
        this.addEventListener("message", event => {
          const frame = JSON.parse(event.data);
          if (frame.capsule) window.latestCheckpoint = frame;
        });
      }
    };
  });
  await page.goto(base);
  const attached = () => page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "true");
  await attached();
  await page.locator("#recovery-draft").fill("keep this draft");
  await page.click("#increment");
  await page.waitForFunction(() => document.getElementById("recovery-count").textContent === "1");
  const firstEpoch = await page.evaluate(() => window.latestCheckpoint.epoch);
  await page.locator("#recovery-draft").evaluate(input => {
    window.originalInput = input; input.focus(); input.setSelectionRange(2, 6);
  });
  await stop();
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "false");
  await page.evaluate(() => document.getElementById("increment").click());
  await start(firstRoot);
  await attached();
  assert.equal(await page.locator("#recovery-count").textContent(), "1");
  assert.equal(await page.locator("#recovery-draft").inputValue(), "keep this draft");
  assert.notEqual(await page.evaluate(() => window.latestCheckpoint.epoch), firstEpoch);
  assert.equal(await page.evaluate(() => window.originalInput === document.getElementById("recovery-draft") &&
    document.activeElement === window.originalInput && window.originalInput.selectionStart === 2 &&
    window.originalInput.selectionEnd === 6), true);
  assert.match(await page.locator("#pysx-connection-status").innerText(), /restored/);
  await page.click("#increment");
  await page.waitForFunction(() => document.getElementById("recovery-count").textContent === "2");
  const secondEpoch = await page.evaluate(() => window.latestCheckpoint.epoch);
  await stop("SIGKILL");
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "false");
  await start(secondRoot);
  await attached();
  assert.equal(await page.locator("#recovery-count").textContent(), "2");
  assert.equal(await page.locator("#recovery-draft").inputValue(), "keep this draft");
  assert.notEqual(await page.evaluate(() => window.latestCheckpoint.epoch), secondEpoch);
  const old = await page.evaluate(() => ({capsule: window.latestCheckpoint.capsule, document: window.latestCheckpoint.document}));
  const oldCookie = (await context.cookies()).find(cookie => cookie.name === "pysx_browser").value;
  await page.evaluate(() => window.dispatchEvent(new Event("pysx:reset")));
  await page.waitForFunction(() => document.getElementById("recovery-count")?.textContent === "0" &&
    document.getElementById("pysx-root").dataset.pysxAttached === "true");
  assert.equal(await page.locator("#recovery-draft").inputValue(), "");
  assert.notEqual((await context.cookies()).find(cookie => cookie.name === "pysx_browser").value, oldCookie);
  const denied = await page.evaluate(saved => new Promise(resolve => {
    const socket = new WebSocket(`ws://${location.host}/ws?protocol=4&recover=1&location=%2F`);
    socket.onopen = () => socket.send(JSON.stringify({t: "hello", version: 4, ...saved}));
    socket.onclose = event => resolve(event.code);
  }), old);
  assert.equal(denied, 4004);
  assert.deepEqual(errors, []);
  console.log("  ok  graceful restart restores latest delivered state, focus and selection without replaying offline clicks");
  console.log("  ok  abrupt worker loss and a relocated compatible deployment reconstruct independent epochs");
  console.log("  ok  anonymous reset rotates the HttpOnly binder and rejects a copied old capsule");
  console.log("CHECKPOINT RESTART BROWSER PASSED");
} finally {
  await browser.close();
  await stop();
  await rm(directory, {recursive: true, force: true});
}
