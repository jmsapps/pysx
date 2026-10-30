import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${process.argv[2]}`);
  await page.waitForFunction(() => document.querySelector("#pysx-root")?.dataset.pysxAttached === "true");
  assert.equal(await page.locator("#hello-example h1").textContent(), "Hello, world!");
  assert.equal(await page.locator("#hello-example").evaluate(node => node.tagName), "DIV");
  console.log("  ok  native greeting mounts into the attached root");
  console.log("HELLO BROWSER PASSED");
} finally { await browser.close(); }
