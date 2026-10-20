import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const base = `http://127.0.0.1:${process.argv[2]}`;
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.addInitScript(() => {
    window.reconnectDelays = [];
    window.applicationCloses = [];
    const NativeSocket = window.WebSocket;
    window.WebSocket = class extends NativeSocket {
      constructor(...args) {
        super(...args);
        if (!String(args[0]).includes("/__pysx_dev__/events")) this.addEventListener("close", event => {
          window.applicationCloses.push({code: event.code, reason: event.reason});
        });
      }
    };
    addEventListener("pysx:connection", event => {
      if (event.detail.state === "reconnecting") window.reconnectDelays.push(event.detail.retryMs);
    });
  });
  const other = await context.newPage();
  const errors = [];
  for (const target of [page, other]) target.on("pageerror", error => errors.push(error.message));
  const connected = target => target.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  const count = (target, value) => target.waitForFunction(value => document.getElementById("count").textContent === String(value), value);
  const label = (target, value) => target.waitForFunction(value => document.getElementById("label").textContent === value, value);
  await page.goto(base);
  await connected(page);
  await other.goto(base); await connected(other);
  assert.equal(await page.locator("#pysx-inspector").count(), 0);
  await page.locator("#increment").click();
  await page.waitForFunction(() => document.getElementById("count").textContent === "1");
  for (let index = 0; index < 3; index++) await other.locator("#increment").click();
  await count(other, 3);
  console.log("  ok  proxied initial render and live websocket events");
  await page.request.get(base + "/__fixture__/edit?action=break");
  await page.waitForFunction(() => document.getElementById("pysx-dev-error")?.textContent.includes("broken browser edit"));
  await page.locator("#increment").click();
  await page.waitForFunction(() => document.getElementById("count").textContent === "2");
  console.log("  ok  failed isolated candidate keeps healthy handlers and reports source failure");
  await page.evaluate(() => { window.__pysxNavigationMarker = "kept"; });
  await page.locator("#field").fill("first draft");
  await page.waitForFunction(() => document.getElementById("echo").textContent === "first draft");
  await page.locator("#uncommitted-check").check();
  await page.locator("#uncommitted-choice").selectOption("b");
  await page.locator("#field").focus();
  await page.evaluate(() => {
    window.retainedField = document.getElementById("field");
    window.retainedField.setSelectionRange(1, 3);
    window.addEventListener("pysx:disconnected", () => {
      window.retainedField.value = "offline edit";
      window.retainedField.setSelectionRange(1, 3);
      window.retainedField.dispatchEvent(new Event("input", {bubbles: true}));
    }, {once: true});
  });
  await page.request.get(base + "/__fixture__/edit?action=repaired");
  await page.waitForFunction(() => document.getElementById("label").textContent === "repaired");
  await connected(page); await connected(other);
  assert.equal(await page.evaluate(() => window.__pysxNavigationMarker), "kept");
  assert.equal(await page.locator("#count").textContent(), "2");
  await count(other, 3);
  assert.equal(await page.evaluate(() => window.retainedField === document.getElementById("field")), true);
  assert.equal(await page.locator("#field").evaluate(el => el === document.activeElement), true);
  assert.deepEqual(await page.locator("#field").evaluate(el => [el.selectionStart, el.selectionEnd]), [1, 3]);
  assert.equal(await page.locator("#field").inputValue(), "first draft");
  assert.equal(await page.locator("#field").evaluate(el => el.defaultValue), "first draft");
  assert.equal(await page.locator("#uncommitted-check").isChecked(), false);
  assert.equal(await page.locator("#uncommitted-choice").inputValue(), "a");
  await page.locator("#handler").click();
  await page.waitForFunction(() => document.getElementById("echo").textContent === "repaired");
  console.log("  ok  compatible nodes, focus and caret survive; stale edits reset and replacement handlers work");
  assert.equal(await page.locator("#pysx-dev-error").count(), 0);
  await page.locator("#increment").click();
  await page.waitForFunction(() => document.getElementById("count").textContent === "3");
  await page.request.get(base + "/__fixture__/edit?action=bad_state");
  await page.waitForFunction(() => document.getElementById("pysx-dev-error")?.textContent.includes("restored state render failed"));
  await page.locator("#increment").click(); await count(page, 4);
  await page.request.get(base + "/__fixture__/edit?action=state-repaired");
  await label(page, "state-repaired"); await label(other, "state-repaired");
  await connected(page); await connected(other);
  await count(page, 4); await count(other, 3);
  assert.ok((await page.evaluate(() => window.reconnectDelays)).length > 0);
  assert.ok((await page.evaluate(() => window.reconnectDelays)).every(delay => delay === 0),
    JSON.stringify(await page.evaluate(() => ({delays: window.reconnectDelays, closes: window.applicationCloses}))));
  console.log("  ok  planned replacement attaches immediately without connection-failure backoff");
  console.log("  ok  restored-state render rejection keeps healthy handlers and isolated tab state, then repairs");
  assert.deepEqual(errors, []);
  assert.equal(await page.locator("#pysx-dev-state").count(), 0);
  assert.equal(await page.locator("#pysx-dev-cold").count(), 0);
  console.log("  ok  repair reconciles without navigating and preserves component state");
  await page.reload(); await connected(page); await count(page, 0);
  await count(other, 3);
  const fresh = await context.newPage();
  await fresh.goto(base); await connected(fresh); await count(fresh, 0);
  await fresh.close();
  const uncached = await context.newPage();
  await uncached.setExtraHTTPHeaders({"Cache-Control": "no-cache"});
  await uncached.goto(base); await connected(uncached); await count(uncached, 0);
  await uncached.close();
  console.log("  ok  unchanged initializers supply fresh defaults on refresh and same-cookie new pages");
  await page.evaluate(() => { window.__pysxNavigationMarker = "discarded"; });
  await page.locator("#increment").click(); await count(page, 1);
  await page.request.get(base + "/__pysx_dev__/cold");
  await page.reload();
  await page.waitForFunction(() => document.getElementById("count")?.textContent === "0");
  assert.equal(await page.evaluate(() => window.__pysxNavigationMarker), undefined);
  assert.equal(await page.locator("#pysx-dev-state").count(), 0);
  console.log("  ok  discarding preserved state cold-starts the page");
  // Restore the fixture so each engine starts from the same source.
  await page.request.get(base + "/__fixture__/edit?action=original");
  await page.waitForFunction(() => document.getElementById("label").textContent === "original");
  await connected(page); await connected(other);
  await page.evaluate(() => { window.reconnectDelays = []; });
  await page.request.get(base + "/__fixture__/edit?action=disconnect");
  await page.waitForFunction(() => window.reconnectDelays.some(delay => delay >= 125));
  await connected(page);
  assert.ok((await page.evaluate(() => window.reconnectDelays)).some(delay => delay >= 125 && delay <= 250));
  console.log("  ok  genuine connection interruptions retain bounded randomized backoff");
  console.log("INSTALLED SERVE BROWSER PASSED");
} finally { await browser.close(); }
