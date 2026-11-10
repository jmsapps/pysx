import assert from "node:assert/strict";
import playwright from "playwright";
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
let page;
const errors = [];
try {
  const context = await browser.newContext();
  page = await context.newPage();
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    window.reconnectTrace = [];
    const record = (type, fields = {}) => {
      window.reconnectTrace.push({type, time: performance.now(),
        readyState: window.testSocket?.readyState,
        connection: document.getElementById("pysx-root")?.dataset.pysxConnection,
        clicks: document.getElementById("reconnect-clicks")?.textContent, ...fields});
      if (window.reconnectTrace.length > 64) window.reconnectTrace.shift();
    };
    for (const type of ["pysx:disconnected", "pysx:connection", "pysx:rejected"])
      window.addEventListener(type, () => record(type));
    const Original = WebSocket;
    window.WebSocket = class extends Original {
      constructor(...args) {
        super(...args); window.testSocket = this;
        this.addEventListener("open", () => record("open"));
        this.addEventListener("close", event => record("close", {code: event.code, reason: event.reason}));
        this.addEventListener("message", event => {
          const frame = JSON.parse(event.data);
          record("received", {frame: frame.t, revision: frame.state_rev});
        });
      }
      send(message) {
        const frame = JSON.parse(message);
        record("sent", {frame: frame.t, handler: frame.h, sequence: frame.seq,
          edits: frame.edits?.length});
        return super.send(message);
      }
      close(...args) {
        const [code, reason] = args;
        record("close-request", {code, reason});
        return super.close(...args);
      }
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
    const status = document.querySelector("#pysx-connection-status");
    window.offlineAttempt = {readyState: window.testSocket.readyState,
      connection: document.getElementById("pysx-root").dataset.pysxConnection};
    document.querySelector("#reconnect-increment").click();
    await new Promise(resolve => setTimeout(resolve, 1000));
    return {status, ticks: Number(document.querySelector("#reconnect-ticks").textContent)};
  });
  assert.equal(away.status, null);
  console.log("  ok  the example refuses offline clicks without injected connection UI");
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
} catch (error) {
  const state = await page?.evaluate(() => ({trace: window.reconnectTrace,
    offlineAttempt: window.offlineAttempt,
    clicks: document.getElementById("reconnect-clicks")?.textContent,
    connection: document.getElementById("pysx-root")?.dataset.pysxConnection,
    readyState: window.testSocket?.readyState})).catch(() => null);
  console.log(`EXAMPLE RECONNECT FAILURE: ${JSON.stringify({state, errors})}`);
  throw error;
} finally { await browser.close(); }
