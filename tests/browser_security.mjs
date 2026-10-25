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
  await first.addInitScript(() => {
    const Original = WebSocket;
    window.WebSocket = class extends Original {
      constructor(...args) { super(...args); window.authSocket = this; }
    };
  });
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
  // Public cookies stand in for application credentials in this local fixture.
  const reconnected = () => first.waitForFunction(() => window.authSocket !== window.priorSocket &&
    document.getElementById("pysx-root").dataset.pysxAttached === "true");
  const dropSocket = () => first.evaluate(() => {
    window.priorSocket = window.authSocket; window.authSocket.close();
  });
  // Without this control every later reset below would also pass with no restoration at all.
  await dropSocket();
  await reconnected();
  assert.equal(await first.locator("#principal").textContent(), "alice");
  assert.equal(await first.locator("#count").textContent(), "1");
  console.log("  ok  an unchanged login generation restores the delivered state across a reconnect");
  // Keep the document and its capsule while changing the login generation.
  await alice.addCookies([{name: "fixture_login", value: "second-login", url: base}]);
  await dropSocket();
  await reconnected();
  await first.waitForFunction(() => document.getElementById("count").textContent === "0");
  assert.equal(await first.locator("#principal").textContent(), "alice");
  await first.click("#increment");
  await first.waitForFunction(() => document.getElementById("count").textContent === "1");
  await alice.clearCookies({name: "fixture_user"});
  assert.equal((await alice.request.get(base)).status(), 403);
  await alice.addCookies([{name: "fixture_user", value: "bob", url: base}]);
  await dropSocket();
  await reconnected();
  await first.waitForFunction(() => document.getElementById("principal").textContent === "bob");
  assert.equal(await first.locator("#count").textContent(), "0");
  console.log("  ok  same-account re-login and logout/login as another account cannot import the previous delivered state");
  console.log("PRINCIPAL ORIGIN BROWSER PASSED");
} finally { await browser.close(); }
