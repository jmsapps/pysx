import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const base = `http://127.0.0.1:${process.argv[2]}`;
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.addInitScript(() => {
    const NativeSocket = window.WebSocket;
    window.WebSocket = class extends NativeSocket {
      constructor(...args) {
        super(...args);
        if (String(args[0]).includes("/__pysx_dev__/events")) window.developmentNotifications = this;
      }
    };
  });
  const other = await context.newPage();
  const connected = target => target.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  await page.goto(base); await connected(page);
  await other.goto(base); await connected(other);
  await page.waitForFunction(() => document.getElementById("pysx-dev-state-text")?.textContent.includes("Workspace:"));
  assert.equal(await page.locator("#pysx-dev-state").count(), 1);
  assert.ok((await page.locator("#pysx-dev-state").textContent()).includes("separate SQLite store"));
  assert.equal(await page.locator("#pysx-dev-cold").textContent(), "Discard development snapshots");
  const inspecting = await page.locator("#pysx-inspector").count() === 1;
  console.log("  ok  authored shared component owns the workspace report and distinguishes declared persistence");
  await page.locator("#durable-increment").click();
  await page.waitForFunction(() => document.getElementById("durable-count").textContent === "1");
  await page.locator("#durable-draft").fill("Saved while editing");
  await page.waitForFunction(() => document.getElementById("durable-echo").textContent === "Saved while editing");
  const marker = await page.locator("#durable-generation").textContent();
  const heading = await page.locator("h1").textContent();
  console.log("  ok  shipped shared-component example serves independently owned state in two tabs");

  await page.request.get(base + "/__fixture__/edit");
  await page.waitForFunction(({marker, heading}) => document.querySelector("h1").textContent !== heading && document.getElementById("durable-generation").textContent !== marker, {marker, heading});
  await connected(page); await connected(other);
  assert.equal(await page.locator("#durable-count").textContent(), "1");
  assert.equal(await other.locator("#durable-count").textContent(), "0");
  assert.equal(await page.locator("#durable-draft").inputValue(), "Saved while editing");
  await page.locator("#durable-increment").click();
  await page.waitForFunction(() => document.getElementById("durable-count").textContent === "2");
  console.log("  ok  editing the real example reconstructs setup while preserving state and working handlers");
  const beforeFallback = await page.locator("#durable-generation").textContent();
  await page.evaluate(() => window.developmentNotifications.close());
  await page.request.get(base + "/__fixture__/edit");
  await page.waitForFunction(marker => document.getElementById("durable-generation").textContent !== marker, beforeFallback);
  await connected(page);
  assert.equal(await page.locator("#durable-count").textContent(), "2");
  assert.equal(await other.locator("#durable-count").textContent(), "0");
  console.log("  ok  closing notification channel falls back to polling without losing application state");

  if (inspecting) {
  await page.locator("#pysx-inspector summary").click();
  await page.waitForFunction(() => document.getElementById("pysx-inspector-content").textContent.includes("[redacted]"));
  assert.ok(await page.locator("#pysx-inspector-content a[href^='vscode://file/']").count() > 0);
  const report = await (await page.request.get(base + "/__pysx_dev__/inspect")).json();
  assert.equal(JSON.stringify(report).includes("Saved while editing"), false);
  console.log("  ok  opt-in inspector presents source links and redacted events for the runnable example");
  await page.locator("#pysx-inspector summary").click();
  } else {
    assert.equal((await page.request.get(base + "/__pysx_dev__/inspect")).status(), 404);
    console.log("  ok  example state panel works with the separate inspector disabled");
  }
  const status = await (await page.request.get(base + "/__pysx_dev__/status")).json();
  assert.equal(JSON.stringify(status).includes("Saved while editing"), false);
  assert.equal(status.scope, "workspace");
  await other.locator("#durable-increment").click();
  await other.waitForFunction(() => document.getElementById("durable-count").textContent === "1");
  await page.evaluate(() => { window.examplePanelMarker = "discard"; });
  await page.locator("#pysx-dev-cold").click();
  await page.waitForFunction(() => document.getElementById("durable-count").textContent === "0");
  await connected(page);
  assert.equal(await page.evaluate(() => window.examplePanelMarker), undefined);
  assert.equal(await other.locator("#durable-count").textContent(), "1");
  assert.equal(await page.locator("#pysx-dev-state").count(), 1);
  console.log("  ok  authored discard starts this page fresh without deleting another tab's declared state");
  console.log("DEVELOPMENT EXAMPLE BROWSER PASSED");
} finally { await browser.close(); }
