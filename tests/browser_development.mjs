import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const base = `http://127.0.0.1:${process.argv[2]}`;
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  const other = await context.newPage();
  const connected = target => target.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  await page.goto(base); await connected(page);
  await other.goto(base); await connected(other);
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

  await page.locator("#pysx-inspector summary").click();
  await page.waitForFunction(() => document.getElementById("pysx-inspector-content").textContent.includes("[redacted]"));
  assert.ok(await page.locator("#pysx-inspector-content a[href^='vscode://file/']").count() > 0);
  const report = await (await page.request.get(base + "/__pysx_dev__/inspect")).json();
  assert.equal(JSON.stringify(report).includes("Saved while editing"), false);
  console.log("  ok  opt-in inspector presents source links and redacted events for the runnable example");
  console.log("DEVELOPMENT EXAMPLE BROWSER PASSED");
} finally { await browser.close(); }
