import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const other = await browser.newPage();
  const url = `http://127.0.0.1:${process.argv[2]}`;
  await Promise.all([page.goto(url), other.goto(url)]);
  await Promise.all([page.waitForSelector('#snapshot'), other.waitForSelector('#snapshot')]);
  assert.equal(await page.locator('#left').textContent(), 'initial');
  assert.equal(await page.locator('#left').evaluate(node => getComputedStyle(node).color), 'rgb(255, 0, 0)');
  assert.equal(await page.locator('#right').evaluate(node => getComputedStyle(node).color), 'rgb(0, 0, 255)');
  assert((await page.locator('#snapshot').textContent()).includes('<unsafe>&'));
  assert.equal(await page.locator('#snapshot unsafe').count(), 0);
  console.log("  ok  mixed snapshots keep namespaces, styles and escaped values");
  await page.locator('#change').click();
  await page.waitForFunction(() => document.querySelector('#live')?.textContent === '<green>');
  assert.equal(await page.locator('#live green').count(), 0);
  assert.equal(await page.locator('#left').textContent(), 'initial');
  assert.equal(await other.locator('#live').textContent(), 'redblue');
  console.log("  ok  raw snapshots stay frozen and live sequences isolate sessions");
  await page.locator('#captured').click();
  await page.waitForFunction(() => document.querySelector('#picked')?.textContent === 'snapshot handler');
  assert.equal(await other.locator('#picked').textContent(), 'none');
  console.log("  ok  snapshot callbacks remain callable without implicit subscriptions");
  console.log("SNAPSHOT BROWSER PASSED");
} finally { await browser.close(); }
