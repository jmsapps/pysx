import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const other = await browser.newPage();
  const url = `http://127.0.0.1:${process.argv[2]}`;
  await Promise.all([page.goto(url), other.goto(url)]);
  await page.waitForSelector("#apply-fix[data-pysx-click]");
  await other.waitForSelector("#apply-fix[data-pysx-click]");
  assert.match(await page.locator("#sample-findings").textContent(), /template-freeze/);
  await page.locator("#apply-fix").click();
  await page.waitForFunction(() => document.querySelector("#sample-findings").textContent.includes("No diagnostics"));
  assert.doesNotMatch(await page.locator("#sample-source").textContent(), /count\(\)/);
  assert.equal(await page.locator("#apply-fix").isDisabled(), true);
  console.log("  ok  guarded public fix repairs and reanalyzes the source sample");
  await page.locator("#show-warning").click();
  await page.waitForFunction(() => document.querySelector("#sample-findings").textContent.includes("native-attribute"));
  assert.equal(await page.locator("#apply-fix").isDisabled(), true);
  await page.locator("#reset-sample").click();
  await page.waitForFunction(() => document.querySelector("#sample-findings").textContent.includes("template-freeze"));
  assert.equal(await page.locator("#apply-fix").isDisabled(), false);
  console.log("  ok  unfixable warning and reset retain accurate action state");
  await page.locator("#increment-counter").click();
  await page.waitForFunction(() => document.querySelector("#live-count").textContent === "1");
  assert.equal(await other.locator("#live-count").textContent(), "0");
  assert.match(await other.locator("#sample-findings").textContent(), /template-freeze/);
  console.log("  ok  live counter and independent tab state survive the workflow");
  console.log("DIAGNOSTICS EXAMPLE BROWSER PASSED");
} finally { await browser.close(); }
