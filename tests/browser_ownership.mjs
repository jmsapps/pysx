import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const base = `http://127.0.0.1:${process.argv[2]}`;
try {
  const context = await browser.newContext();
  const reset = await context.request.get(base + "/_owner_control/reset");
  assert.equal(reset.status(), 200);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    const Original = WebSocket;
    window.socketCount = 0; window.states = [];
    Math.random = () => 1;
    window.addEventListener("pysx:connection", event => window.states.push(event.detail));
    window.WebSocket = class extends Original {
      constructor(...args) {
        super(...args); window.testSocket = this; window.socketCount++;
        this.addEventListener("message", event => {
          const frame = JSON.parse(event.data);
          if (frame.owner) window.owner = frame.owner;
        });
      }
    };
  });
  const connected = async () => page.waitForFunction(() =>
    document.getElementById("pysx-root").dataset.pysxConnection === "connected", null, {timeout: 10000});
  const control = async (action, extra = "") => {
    const owner = await page.evaluate(() => window.owner);
    const response = await context.request.get(`${base}/_owner_control/${action}?worker=${encodeURIComponent(owner)}${extra}`);
    assert.equal(response.status(), 200);
  };
  const response = await page.goto(base + "/deep/link");
  assert.equal(response.status(), 200);
  const html = await response.text();
  assert.ok(html.includes("Durable counter"));
  assert.ok(html.includes("data-pysx-recovery="));
  assert.ok(html.includes("data-pysx-owner="));
  await connected();
  await page.locator("#durable-increment").click();
  await page.waitForFunction(() => document.getElementById("durable-count").textContent === "1");
  const generation = await page.locator("#durable-generation").textContent();
  const count = await page.evaluate(() => {
    window.retained = document.getElementById("durable-count");
    const count = window.socketCount; window.testSocket.close(); return count;
  });
  await page.waitForFunction(count => window.socketCount > count &&
    document.getElementById("pysx-root").dataset.pysxConnection === "connected", count);
  assert.equal(await page.locator("#durable-generation").textContent(), generation);
  assert.equal(await page.evaluate(() => window.retained === document.getElementById("durable-count")), true);
  const listeners = await page.evaluate(() => window.states.filter(state => state.state === "connected").at(-1).listeners);
  assert.equal(await page.evaluate(() => window.testSocket.url.includes("recovery=")), false);
  console.log("  ok  meaningful SSR and worker hints route adoption and same-owner resume without new resources");

  await control("kill");
  await page.waitForFunction(generation => document.getElementById("pysx-root").dataset.pysxConnection === "connected" &&
    document.getElementById("durable-generation").textContent !== generation, generation, {timeout: 10000});
  assert.equal(await page.locator("#durable-count").textContent(), "1");
  assert.equal(await page.evaluate(() => window.retained.isConnected), false);
  assert.match(await page.locator("#pysx-connection-status").textContent(), /restored/);
  assert.equal(await page.evaluate(() => window.states.filter(state => state.state === "connected").at(-1).listeners), listeners);
  await page.locator("#durable-increment").click();
  await page.waitForFunction(() => document.getElementById("durable-count").textContent === "2");
  console.log("  ok  killed owner reconstructs committed state with fresh handlers, nodes and one listener set");

  const beforeUpgrade = await page.locator("#durable-generation").textContent();
  await control("restart", "&schema=2&migrate=1");
  await page.waitForFunction(generation => document.getElementById("pysx-root").dataset.pysxConnection === "connected" &&
    document.getElementById("durable-generation").textContent !== generation, beforeUpgrade, {timeout: 10000});
  assert.equal(await page.locator("#durable-count").textContent(), "2");
  await page.locator("#durable-increment").click();
  await page.waitForFunction(() => document.getElementById("durable-count").textContent === "3");
  console.log("  ok  rolling restart migrates compatible declared state and rebuilds working handlers");

  await control("restart", "&schema=3&fresh=1");
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected" &&
    document.getElementById("durable-count").textContent === "0", null, {timeout: 10000});
  assert.match(await page.locator("#pysx-connection-status").textContent(), /fresh session/);
  await page.locator("#durable-increment").click();
  await page.waitForFunction(() => document.getElementById("durable-count").textContent === "1");
  assert.deepEqual(errors, []);
  assert.ok(await page.evaluate(() => window.states.every(state => state.timers <= 1 && state.pendingBytes <= 65536)));
  console.log("  ok  incompatible opt-in fresh recovery visibly resets state and keeps bounded reconnect ownership");
  console.log("OWNER ROUTING BROWSER PASSED");
  await context.close();
} finally { await browser.close(); }
