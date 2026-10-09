import assert from "node:assert/strict";
import playwright from "playwright";
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${process.argv[2]}`);
  await page.waitForSelector("#snapshot-field");
  await page.locator("#snapshot-create").click();
  await page.waitForFunction(() => document.querySelector("#snapshot-status")?.textContent === "created");
  await page.evaluate(() => {
    window.savedSnapshot = [document.querySelector("#snapshot-row"), document.querySelector("#snapshot-field"), document.querySelector("#snapshot-zone"), document.querySelector("#owned-child")];
  });
  await page.locator("#snapshot-live").click();
  await page.waitForFunction(() => document.querySelector("#snapshot-label")?.textContent === "live");
  assert.equal(await page.locator("#snapshot-field").inputValue(), "live");
  await page.locator("#snapshot-replace").click();
  await page.waitForSelector("#snapshot-row > p");
  assert.equal(await page.locator("#snapshot-label").textContent(), "seed");
  assert.equal(await page.locator("#snapshot-label").getAttribute("title"), "seed");
  assert.equal(await page.locator("#snapshot-field").inputValue(), "seed");
  assert.equal(await page.evaluate(() => window.savedSnapshot[0] === document.querySelector("#snapshot-row") && window.savedSnapshot[1] === document.querySelector("#snapshot-field")), true);
  console.log("  ok  later structural/rebinding patches compare current live text and properties");
  assert.equal(await page.evaluate(() => window.savedSnapshot[2] === document.querySelector("#snapshot-zone") && window.savedSnapshot[3] === document.querySelector("#owned-child")), true);
  await page.locator("#snapshot-inspect").click();
  await page.waitForFunction(() => document.querySelector("#snapshot-status")?.textContent === "retained");
  await page.locator("#owned-child").click();
  await page.waitForFunction(() => document.querySelector("#snapshot-clicks")?.textContent === "1");
  assert.deepEqual(errors, []);
  console.log("  ok  ancestor morph retains imperative content, handles and listeners");
  await page.locator("#snapshot-remount").click();
  await page.waitForFunction(() => !document.querySelector("#snapshot-zone")?.hasAttribute("data-pysx-imperative"));
  assert.equal(await page.evaluate(() => window.savedSnapshot[0] === document.querySelector("#snapshot-row") && window.savedSnapshot[2] !== document.querySelector("#snapshot-zone") && !window.savedSnapshot[3].isConnected), true);
  assert.deepEqual(errors, []);
  console.log("  ok  a changed ref owner replaces its same-tag target and releases imperative content");
  console.log("KEYED SNAPSHOT BROWSER PASSED");
} finally { await browser.close(); }
