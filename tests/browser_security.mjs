import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const base = `http://127.0.0.1:${process.argv[2]}`;
try {
  const anonymous = await browser.newContext();
  const denied = await anonymous.request.get(base);
  assert.equal(denied.status(), 403);
  assert.equal(await denied.text(), "access denied");
  console.log("  ok  unauthenticated HTTP cannot render a private graph");
  const alice = await browser.newContext();
  const bob = await browser.newContext();
  await alice.addCookies([{ name: "fixture_user", value: "alice", url: base }]);
  await bob.addCookies([{ name: "fixture_user", value: "bob", url: base }]);
  const first = await alice.newPage();
  const second = await bob.newPage();
  await Promise.all([first.goto(base), second.goto(base)]);
  await first.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "true");
  await second.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "true");
  assert.equal(await first.locator("#principal").textContent(), "alice");
  assert.equal(await second.locator("#principal").textContent(), "bob");
  await first.locator("#increment").click();
  await first.waitForFunction(() => document.getElementById("count").textContent === "1");
  assert.equal(await second.locator("#count").textContent(), "0");
  await second.locator("#increment").click();
  await second.waitForTimeout(150);
  assert.equal(await second.locator("#count").textContent(), "0");
  assert.equal(await first.locator("#count").textContent(), "1");
  console.log("  ok  principal authorization rejects a mutation and preserves sibling state");
  // A foreign Origin arrives through an actual browser socket from a separate HTTP origin.
  const foreign = await alice.newPage();
  await foreign.route("http://foreign.test/", route => route.fulfill({body: "<html></html>", contentType: "text/html"}));
  await foreign.goto("http://foreign.test/");
  const rejected = await foreign.evaluate(url => new Promise(resolve => {
    const socket = new WebSocket(url.replace("http:", "ws:") + "/ws?protocol=3");
    socket.onopen = () => { socket.close(); resolve(false); };
    socket.onerror = () => resolve(true);
  }), base);
  assert.equal(rejected, true);
  console.log("  ok  actual cross-origin browser upgrade fails before attachment");
  console.log("PRINCIPAL ORIGIN BROWSER PASSED");
} finally { await browser.close(); }
