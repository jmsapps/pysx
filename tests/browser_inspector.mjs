import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const base = `http://127.0.0.1:${process.argv[2]}`;
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(base);
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  await page.locator("#pysx-inspector summary").click();
  await page.waitForFunction(() => document.getElementById("pysx-inspector-content").textContent.includes("[redacted]"));
  for (let index = 0; index < 40; index++) await page.locator("#increment").click();
  await page.waitForFunction(() => document.getElementById("count").textContent === "40");
  const data = await (await page.request.get(base + "/__pysx_dev__/inspect")).json();
  assert.equal(data.sessions.length, 1);
  assert.equal(data.sessions[0].events.length, 256);
  assert.ok(data.sessions[0].dropped > 0);
  assert.ok(data.sessions[0].counts["patch.batch"] >= 40);
  assert.equal(JSON.stringify(data).includes("sensitive-inspector-token"), false);
  assert.ok(await page.locator("#pysx-inspector-content a[href^='vscode://file/']").count() > 0);
  console.log("  ok  source-linked inspector shows actual graph/patch events with bounded buffers and redacted values");

  const stranger = await browser.newContext();
  assert.equal((await stranger.request.get(base + "/__pysx_dev__/inspect")).status(), 403);
  const other = await stranger.newPage(); await other.goto(base);
  await other.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  const own = await (await other.request.get(base + "/__pysx_dev__/inspect")).json();
  assert.equal(own.sessions.length, 1);
  assert.notEqual(own.sessions[0].id, data.sessions[0].id);
  assert.equal(JSON.stringify(own).includes(data.sessions[0].id), false);
  await stranger.close();
  console.log("  ok  an unbound browser is denied and a separate browser sees only its own session");

  await page.request.get(base + "/__fixture__/diagnostic");
  await page.waitForFunction(async () => {
    const response = await fetch("/__pysx_dev__/inspect");
    if (!response.ok) return false;
    const report = await response.json();
    return !report.analysis.pending && report.analysis.files.some(file => file.path.endsWith("advisory.py") && file.diagnostics.length > 0);
  });
  await page.locator("#pysx-inspector button").click();
  await page.waitForFunction(() => document.getElementById("pysx-inspector-content").textContent.includes("noSuchAttribute"));
  assert.equal(await page.locator("#count").textContent(), "40");
  assert.equal(await page.locator("#pysx-dev-state").count(), 0);
  assert.equal(await page.locator("#pysx-dev-cold").count(), 0);
  assert.deepEqual(errors, []);
  console.log("  ok  current-source checker diagnostics appear with exact codes/ranges while the hot app retains state");
  console.log("INSPECTOR TRACING BROWSER PASSED");
} finally { await browser.close(); }
