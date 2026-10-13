import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage(), other = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const url = `http://127.0.0.1:${process.argv[2]}/`;
  await page.goto(url); await other.goto(url);
  await page.waitForSelector("#panel"); await other.waitForSelector("#panel");
  await page.locator("#healthy-field").fill("Retained text");
  await page.evaluate(() => { window.sibling = document.getElementById("healthy-panel"); });
  for (let turn = 1; turn <= 3; turn++) {
    await page.locator("#increment-panel").click();
    await page.waitForFunction(() => document.getElementById("panel-count").textContent === "1");
    await page.locator("#fail-panel").click();
    await page.waitForSelector("#fallback");
    await page.waitForFunction(turn => document.getElementById("cleanups").textContent === String(turn), turn);
    assert.equal(await page.locator("#attempts").textContent(), String(turn));
    await page.locator("#increment-healthy").click();
    await page.waitForFunction(turn => document.getElementById("healthy-count").textContent === String(turn), turn);
    await page.locator("#healthy-field").focus();
    await page.evaluate(() => document.getElementById("retry").click());
    await page.waitForSelector("#panel");
    assert.equal(await page.locator("#panel-count").textContent(), "0");
    assert.equal(await page.locator("#healthy-field").inputValue(), "Retained text");
    assert.equal(await page.evaluate(() => window.sibling === document.getElementById("healthy-panel")), true);
    assert.equal(await page.locator("#healthy-field").evaluate(node => node === document.activeElement), true);
    console.log(`  ok  recovery cycle ${turn} retains sibling identity/focus, commits failure count and disposes the panel once`);
  }
  assert.equal(await other.locator("#healthy-count").textContent(), "0");
  assert.equal(await other.locator("#attempts").textContent(), "0");
  assert.deepEqual(errors, []);
  console.log("  ok  recovery example isolates tab state and avoids duplicate healthy handlers");
  console.log("FALLBACK EXAMPLE BROWSER PASSED");
} finally { await browser.close(); }
