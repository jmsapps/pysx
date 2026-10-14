import assert from "node:assert/strict";
import playwright from "playwright";
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const base = `https://127.0.0.1:${process.argv[2]}`;
try {
  const context = await browser.newContext({ignoreHTTPSErrors: true});
  await context.addCookies([{name: "fixture_user", value: "alice", url: base, secure: true, httpOnly: true, sameSite: "Lax"}]);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    const Original = WebSocket;
    window.socketCount = 0;
    window.WebSocket = class extends Original {
      constructor(...args) { super(...args); window.testSocket = this; window.socketCount++; }
    };
  });
  const response = await page.goto(base + "/secure/deep/link");
  assert.equal(response.status(), 200);
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  assert.ok(await page.evaluate(() => window.testSocket.url.startsWith("wss:") && window.testSocket.url.includes("/secure/ws")));
  const cookie = (await context.cookies()).find(cookie => cookie.name === "pysx_browser");
  assert.equal(cookie.secure, true); assert.equal(cookie.httpOnly, true); assert.equal(cookie.sameSite, "Lax");
  assert.ok(response.headers()["x-request-id"]);
  console.log("  ok  production TLS deep link uses secure cookies and WSS through an explicit proxy");
  await page.locator("#increment").click();
  await page.waitForFunction(() => document.getElementById("count").textContent === "1");
  await page.evaluate(() => { window.retained = document.getElementById("count"); window.testSocket.close(); });
  await page.waitForFunction(() => window.socketCount >= 2 && document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  assert.equal(await page.locator("#count").textContent(), "1");
  assert.equal(await page.evaluate(() => window.retained === document.getElementById("count")), true);
  await page.reload();
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  assert.equal(await page.locator("#count").textContent(), "0");
  assert.deepEqual(errors, []);
  console.log("  ok  secure resume retains one graph and reload independently renders a fresh graph");
  console.log("DEPLOYMENT SECURITY BROWSER PASSED");
} finally { await browser.close(); }
