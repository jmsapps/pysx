import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${process.argv[2]}`);
  await page.waitForSelector("#increment[data-pysx-click]");
  assert.equal(await page.locator("#advisory").getAttribute("hreff"), "preserved");
  assert.equal(await page.locator("#total").textContent(), "0");
  console.log("  ok  advisory attribute remains in the rendered DOM");
  await page.locator("#increment").click();
  await page.waitForFunction(() => document.querySelector("#total").textContent === "1");
  console.log("  ok  warned template retains live events and Signal updates");
  console.log("DIAGNOSTICS ACTIONS BROWSER PASSED");
} finally { await browser.close(); }
