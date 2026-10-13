import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${process.argv[2]}/`);
  await page.waitForFunction(() => document.getElementById("value").textContent === "result-1");
  await page.waitForFunction(() => document.getElementById("timer").textContent === "1");
  assert.equal(await page.locator("#status").textContent(), "success");
  console.log("  ok  timer and completed resource push without browser input");
  await page.locator("#reload").click();
  await page.waitForFunction(() => document.getElementById("status").textContent === "pending");
  await page.locator("#reload").click();
  await page.waitForFunction(() => document.getElementById("value").textContent === "result-3");
  await page.waitForTimeout(200);
  assert.equal(await page.locator("#value").textContent(), "result-3");
  assert.deepEqual(errors, []);
  console.log("  ok  overlapping requests discard cancellation-resistant stale completion");
  console.log("SERIALIZED ASYNC BROWSER PASSED");
} finally { await browser.close(); }
