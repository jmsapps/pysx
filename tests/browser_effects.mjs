import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${process.argv[2]}`);
  const history = () => page.locator("#effect-history").textContent();
  await page.waitForFunction(() => document.querySelector("#effect-history")?.textContent === "run 0");
  await page.locator("#effect-increment").click();
  await page.waitForFunction(() => document.querySelector("#effect-history").textContent === "run 0 | cleanup 0 | run 1");
  await page.locator("#effect-toggle").click();
  await page.waitForFunction(() => !document.querySelector("#effect-observer"));
  assert((await history()).endsWith("cleanup 1"));
  const disposed = await history();
  await page.locator("#effect-increment").click();
  await page.waitForFunction(() => document.querySelector("#effect-count").textContent === "2");
  assert.equal(await history(), disposed);
  await page.locator("#effect-toggle").click();
  await page.waitForFunction(() => document.querySelector("#effect-history").textContent.endsWith("run 2"));
  console.log("  ok  effect cleanup precedes rerun and disposal removes the subscription");
  await page.locator("#effect-auto").click();
  await page.waitForFunction(() => Number(document.querySelector("#effect-count").textContent) > 2);
  await page.locator("#effect-auto").click();
  await page.waitForFunction(() => document.querySelector("#effect-running").textContent === "False");
  const stopped = await page.locator("#effect-count").textContent();
  // Two full tick periods prove the stopped runner cannot mutate the count.
  await page.waitForTimeout(1500);
  assert.equal(await page.locator("#effect-count").textContent(), stopped);
  console.log("  ok  automatic increments stop across later timer deadlines");
  await page.locator("#effect-trigger").click();
  await page.waitForFunction(() => document.querySelector("#effect-delayed").textContent === "pending");
  await page.locator("#effect-cancel").click();
  await page.waitForFunction(() => document.querySelector("#effect-delayed").textContent === "cancelled");
  await page.waitForTimeout(1300);
  assert.equal(await page.locator("#effect-delayed").textContent(), "cancelled");
  await page.locator("#effect-trigger").click();
  await page.waitForFunction(() => document.querySelector("#effect-delayed").textContent === "pending");
  await page.locator("#effect-trigger").click();
  await page.waitForFunction(() => document.querySelector("#effect-delayed").textContent === "Finished 4");
  assert((await history()).split(" | ").length <= 10);
  console.log("  ok  cancelled and superseded delayed loads cannot publish stale results; logs are bounded");
  console.log("EFFECTS BROWSER PASSED");
} finally { await browser.close(); }
