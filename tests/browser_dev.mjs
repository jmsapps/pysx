import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const base = `http://127.0.0.1:${process.argv[2]}`;
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(base);
  await page.waitForSelector('[data-pysx-attached="true"]');
  assert.equal(await page.locator("#pysx-inspector").count(), 0);
  await page.locator("#increment").click();
  await page.waitForFunction(() => document.getElementById("count").textContent === "1");
  console.log("  ok  proxied initial render and live websocket events");
  await page.request.get(base + "/__fixture__/edit?action=break");
  await page.waitForFunction(() => document.getElementById("pysx-dev-error")?.textContent.includes("broken browser edit"));
  await page.locator("#increment").click();
  await page.waitForFunction(() => document.getElementById("count").textContent === "2");
  console.log("  ok  failed isolated candidate keeps healthy handlers and reports source failure");
  await page.evaluate(() => { window.__pysxNavigationMarker = "kept"; });
  await page.request.get(base + "/__fixture__/edit?action=repaired");
  await page.waitForFunction(() => document.getElementById("label").textContent === "repaired");
  await page.waitForSelector('[data-pysx-attached="true"]');
  assert.equal(await page.evaluate(() => window.__pysxNavigationMarker), "kept");
  assert.equal(await page.locator("#count").textContent(), "2");
  assert.equal(await page.locator("#pysx-dev-error").count(), 0);
  await page.locator("#increment").click();
  await page.waitForFunction(() => document.getElementById("count").textContent === "3");
  assert.deepEqual(errors, []);
  await page.waitForFunction(() => document.getElementById("pysx-dev-state")?.textContent.includes("preserved"));
  assert.equal(await page.locator("#pysx-dev-cold").count(), 1);
  console.log("  ok  repair reconciles without navigating and preserves component state");
  await page.locator("#pysx-dev-cold").click();
  await page.waitForFunction(() => document.getElementById("count")?.textContent === "0");
  assert.equal(await page.evaluate(() => window.__pysxNavigationMarker), undefined);
  console.log("  ok  discarding preserved state cold-starts the page");
  // Restore the fixture so each engine starts from the same source.
  await page.request.get(base + "/__fixture__/edit?action=original");
  await page.waitForFunction(() => document.getElementById("label").textContent === "original");
  console.log("INSTALLED SERVE BROWSER PASSED");
} finally { await browser.close(); }
