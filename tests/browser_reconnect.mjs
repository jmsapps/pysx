import assert from "node:assert/strict";
import playwright from "playwright";
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const legacy = process.env.PYSX_BROWSER_CASE === "legacy_expired_reconnect_fixture";
const expired = legacy || process.env.PYSX_BROWSER_CASE === "expired_reconnect_fixture";
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => {
    const status = /Unexpected response code: (\d+)/.exec(message.text());
    if (status) errors.push(`handshake status ${status[1]}`);
  });
  await page.addInitScript(() => {
    const Original = WebSocket;
    window.testSockets = [];
    window.testStates = [];
    window.testRejected = 0;
    window.testCloses = [];
    window.testEndpoints = [];
    Math.random = () => 1;
    window.addEventListener("pysx:connection", event => window.testStates.push(event.detail));
    window.addEventListener("pysx:rejected", () => window.testRejected++);
    window.WebSocket = class extends Original {
      constructor(...args) {
        super(...args); window.testSocket = this; window.testSockets.push(this);
        const address = new URL(args[0]);
        window.testEndpoints.push({host: address.host, path: address.pathname, version: address.searchParams.get("protocol")});
        this.addEventListener("close", event => window.testCloses.push({code: event.code, reason: event.reason}));
      }
    };
  });
  const url = `http://127.0.0.1:${process.argv[2]}/`;
  await page.goto(url);
  await page.waitForFunction(() => document.getElementById("pysx-root")?.dataset.pysxConnection === "connected").catch(async error => {
    const diagnostic = await page.evaluate(() => ({states: window.testStates, closes: window.testCloses, endpoints: window.testEndpoints,
      ready: document.readyState, status: document.getElementById("pysx-connection-status")?.textContent}));
    throw new Error(`${error.message}; ${JSON.stringify(diagnostic)}; errors=${JSON.stringify(errors)}`);
  });
  const generation = await page.locator("#reconnect-generation").textContent();
  await page.locator("#reconnect-increment").click();
  await page.waitForFunction(() => document.querySelector("#reconnect-clicks").textContent === "1");
  await page.evaluate(async () => {
    window.savedField = document.querySelector("#reconnect-field");
    window.savedField.focus(); window.savedField.setSelectionRange(1, 4);
    const lost = new Promise(resolve => window.addEventListener("pysx:disconnected", resolve, {once: true}));
    window.testSocket.close(); await lost;
    window.savedField.value = "unsent offline draft";
    window.savedField.dispatchEvent(new Event("input", {bubbles: true}));
    document.querySelector("#reconnect-increment").click();
    window.dispatchEvent(new Event("hashchange"));
    window.statusDuringLoss = document.querySelector("#pysx-connection-status");
  });
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  assert.equal(await page.evaluate(() => window.statusDuringLoss), null);
  assert.equal(await page.locator("#reconnect-field").inputValue(), "server draft");
  assert.ok(await page.evaluate(() => window.testRejected >= 2));
  assert.ok(await page.evaluate(() => window.testStates.every(state => state.timers <= 1 && state.pendingBytes <= 65536)));
  if (expired) {
    assert.notEqual(await page.locator("#reconnect-generation").textContent(), generation);
    assert.equal(await page.locator("#reconnect-clicks").textContent(), legacy ? "0" : "1");
    assert.equal(await page.locator("#pysx-connection-status").count(), 0);
    assert.equal(await page.evaluate(() => window.savedField.isConnected), !legacy);
    console.log(legacy ? "  ok  legacy v3 expiry starts fresh without injected UI and drops unsent events" :
      "  ok  v4 expiry reconstructs delivered state into a new graph and drops unsent events");
  } else {
    assert.equal(await page.locator("#reconnect-generation").textContent(), generation);
    assert.equal(await page.locator("#reconnect-clicks").textContent(), "1");
    assert.equal(await page.evaluate(() => window.savedField === document.querySelector("#reconnect-field") && document.activeElement === window.savedField), true);
    const listenerCount = await page.evaluate(() => window.testStates.filter(state => state.state === "connected").at(-1).listeners);
    for (let index = 0; index < 3; index++) {
      const count = await page.evaluate(() => window.testSockets.length);
      await page.evaluate(() => window.testSocket.close());
      await page.waitForFunction(count => window.testSockets.length > count && document.getElementById("pysx-root").dataset.pysxConnection === "connected", count);
    }
    assert.equal(await page.evaluate(() => window.testStates.filter(state => state.state === "connected").at(-1).listeners), listenerCount);
    await page.locator("#reconnect-increment").click();
    await page.waitForFunction(() => document.querySelector("#reconnect-clicks").textContent === "2");
    console.log("  ok  bounded automatic resume retains graph and nodes without replay or listener growth");
  }
  const other = await context.newPage();
  await other.goto(url);
  await other.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  assert.notEqual(await other.locator("#reconnect-generation").textContent(), await page.locator("#reconnect-generation").textContent());
  assert.equal(await other.locator("#reconnect-clicks").textContent(), "0");
  assert.deepEqual(errors, []);
  console.log("  ok  tabs sharing a browser cookie retain independent session state");
  if (!expired) {
    const policy = await context.newPage();
    await policy.addInitScript(() => {
      Math.random = () => 1;
      window.retryStates = [];
      window.addEventListener("pysx:connection", event => window.retryStates.push(event.detail));
      const original = setTimeout;
      window.setTimeout = (callback, delay, ...args) => original(callback,
        delay >= 250 && delay <= 10000 ? delay * 0.01 : delay, ...args);
    });
    let attempts = 0;
    await policy.routeWebSocket("**/ws?*", route => {
      attempts++;
      route.onMessage(() => route.close({code: 1013, reason: "unavailable"}));
    });
    await policy.goto(url);
    await policy.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "disconnected");
    assert.equal(attempts, 9);
    assert.ok(await policy.evaluate(() => window.retryStates.every(state => state.timers <= 1 && state.retryMs <= 10000)));
    assert.equal(await policy.evaluate(() => window.retryStates.at(-1).timers), 0);
    assert.equal(await policy.getByRole("button", {name: "Retry connection"}).count(), 0);
    await policy.close();
    console.log("  ok  capped retry exhaustion leaves no transport timer or injected retry UI");
    const broken = await context.newPage();
    await broken.addInitScript(() => {
      window.retryAttempts = 0;
      window.addEventListener("pysx:connection", () => window.retryAttempts++);
    });
    let sockets = 0;
    await broken.routeWebSocket("**/ws?*", route => {
      sockets++;
      route.onMessage(() => route.close({code: 4002, reason: "invalid envelope"}));
    });
    await broken.goto(url);
    await broken.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "stopped");
    assert.equal(await broken.getByRole("button", {name: "Retry connection"}).count(), 0);
    assert.equal(await broken.locator("#pysx-connection-status").count(), 0);
    await broken.evaluate(() => window.dispatchEvent(new CustomEvent("pysx:reconnect")));
    await broken.waitForTimeout(250);
    assert.equal(sockets, 1);
    assert.equal(await broken.evaluate(() => document.getElementById("pysx-root").dataset.pysxConnection), "stopped");
    await broken.close();
    console.log("  ok  a protocol failure reports a distinct stopped state and offers no inert retry");
  }
  console.log("BOUNDED RECONNECT BROWSER PASSED");
} finally { await browser.close(); }
