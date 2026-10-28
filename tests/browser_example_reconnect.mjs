import assert from "node:assert/strict";
import playwright from "playwright";
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    const Original = WebSocket;
    window.WebSocket = class extends Original {
      constructor(...args) { super(...args); window.testSocket = this; }
    };
  });
  const url = `http://127.0.0.1:${process.argv[2]}/`;
  await page.goto(url);
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  assert.equal(await page.evaluate(() => document.getElementById("pysx-connection-status")), null);
  console.log("  ok  a healthy connected page carries no injected connection markup");
  const session = await page.locator("#reconnect-generation").textContent();
  await page.locator("#reconnect-increment").click();
  await page.waitForFunction(() => document.querySelector("#reconnect-clicks").textContent === "1");
  const reading = () => page.evaluate(() => Number(document.querySelector("#reconnect-ticks").textContent));
  const away = await page.evaluate(async () => {
    const field = document.querySelector("#reconnect-field");
    field.focus(); field.setSelectionRange(2, 6);
    const lost = new Promise(resolve => window.addEventListener("pysx:disconnected", resolve, {once: true}));
    window.testSocket.close(); await lost;
    const status = document.querySelector("#pysx-connection-status").textContent;
    document.querySelector("#reconnect-increment").click();
    await new Promise(resolve => setTimeout(resolve, 1000));
    return {status, ticks: Number(document.querySelector("#reconnect-ticks").textContent)};
  });
  assert.match(away.status, /Disconnected/);
  console.log("  ok  the example reports a disconnected status and refuses offline clicks");
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  assert.equal(await page.locator("#reconnect-generation").textContent(), session);
  assert.equal(await page.locator("#reconnect-clicks").textContent(), "1");
  assert.equal(await page.evaluate(() => document.activeElement === document.querySelector("#reconnect-field")), true);
  console.log("  ok  the example resumes the same session without replaying offline clicks");
  await page.waitForFunction(ticks => Number(document.querySelector("#reconnect-ticks").textContent) > ticks, away.ticks);
  assert.ok(await reading() > away.ticks);
  await page.locator("#reconnect-increment").click();
  await page.waitForFunction(() => document.querySelector("#reconnect-clicks").textContent === "2");
  assert.deepEqual(errors, []);
  console.log("  ok  the example adopts the current server clock and stays interactive");
  console.log("EXAMPLE RECONNECT BROWSER PASSED");
} finally { await browser.close(); }
