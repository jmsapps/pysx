import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const base = `http://127.0.0.1:${process.argv[2]}`;
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on("pageerror", error => console.error(error));
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
  assert.ok((await page.locator("#pysx-dev-state").textContent()).includes("No database is used"));
  assert.equal(await page.locator("#pysx-dev-cold").textContent(), "Discard development snapshots");
  const inspecting = await page.locator("#pysx-inspector").count() === 1;
  const count = async (target, value) => {
    await target.waitForFunction(value => document.getElementById("development-count").textContent === String(value)
      && document.getElementById("development-state-count").textContent === String(value), value);
  };
  console.log("  ok  authored shared panel binds the public sample count and owns redacted workspace metadata");
  for (const target of [page, other]) {
    const initial = await target.locator("#development-count").textContent();
    const panel = await target.locator("#development-state-count").textContent();
    assert.equal(initial, "0", `initial counter: ${initial}; panel: ${panel}`);
    assert.equal(panel, "0", `initial counter: ${initial}; panel: ${panel}`);
  }
  await page.locator("#development-increment").click();
  await count(page, 1);
  const heading = await page.locator("h1").textContent();
  await page.evaluate(() => { window.examplePanelMarker = "hot update"; });

  await page.request.get(base + "/__fixture__/edit");
  for (const target of [page, other]) await target.waitForFunction(heading => document.querySelector("h1").textContent !== heading, heading);
  await connected(page); await connected(other);
  await count(page, 1); await count(other, 0);
  assert.equal(await page.evaluate(() => window.examplePanelMarker), "hot update");
  await page.waitForFunction(() => /preserved [1-9]/.test(document.getElementById("pysx-dev-state-text").textContent));
  await page.locator("#development-increment").click();
  await count(page, 2);
  await page.reload(); await connected(page); await count(page, 0);
  assert.equal(await page.evaluate(() => window.examplePanelMarker), undefined);
  const fresh = await context.newPage();
  await fresh.goto(base); await connected(fresh); await count(fresh, 0);
  await fresh.close();
  console.log("  ok  documented increment/title-edit/retain/increment/refresh sequence resets local state and new tabs start fresh");
  const beforeFallback = await page.locator("h1").textContent();
  await page.evaluate(() => window.developmentNotifications.close());
  await page.request.get(base + "/__fixture__/edit");
  await page.waitForFunction(heading => document.querySelector("h1").textContent !== heading, beforeFallback);
  await connected(page);
  await count(page, 0); await count(other, 0);
  console.log("  ok  closing notification channel falls back to polling without losing application state");

  await page.locator("#development-increment").click();
  await page.locator("#development-increment").click();
  await count(page, 2);
  await page.request.get(base + "/__fixture__/edit?action=initializer");
  await count(page, 1); await count(other, 1);
  await page.waitForFunction(() => document.getElementById("pysx-dev-state-resets").textContent.includes("initializer changed"));
  assert.ok(await page.locator("#pysx-dev-state-resets a[href^='vscode://file/']").count() > 0);
  await page.locator("#development-increment").click(); await count(page, 2);
  console.log("  ok  changing the ordinary signal initializer resets its value and the panel reports why");

  if (inspecting) {
  await page.locator("#pysx-inspector summary").click();
  await page.waitForFunction(() => document.getElementById("pysx-inspector-content").textContent.includes("[redacted]"));
  await page.locator("#pysx-inspector-content a[href^='vscode://file/']").first().waitFor();
  const report = await (await page.request.get(base + "/__pysx_dev__/inspect")).json();
  assert.ok(JSON.stringify(report).includes("[redacted]"));
  console.log("  ok  opt-in inspector presents source links and redacted events for the runnable example");
  await page.locator("#pysx-inspector summary").click();
  } else {
    assert.equal((await page.request.get(base + "/__pysx_dev__/inspect")).status(), 404);
    console.log("  ok  example state panel works with the separate inspector disabled");
  }
  const status = await (await page.request.get(base + "/__pysx_dev__/status")).json();
  assert.equal(status.scope, "workspace");
  await count(page, 2);
  await other.locator("#development-increment").click(); await count(other, 2);
  await page.evaluate(() => { window.examplePanelMarker = "discard"; });
  await page.locator("#pysx-dev-cold").click();
  await count(page, 1);
  await connected(page);
  assert.equal(await page.evaluate(() => window.examplePanelMarker), undefined);
  await count(other, 2);
  assert.equal(await page.locator("#pysx-dev-state").count(), 1);
  assert.equal(await (await page.request.get(base + "/__fixture__/persistence")).text(), "no store");
  console.log("  ok  discard uses the current default, preserves another live tab and needs no persistence store");
  await page.request.get(base + "/__fixture__/edit?action=restore");
  await count(page, 0); await count(other, 0);
  console.log("DEVELOPMENT EXAMPLE BROWSER PASSED");
} finally { await browser.close(); }
