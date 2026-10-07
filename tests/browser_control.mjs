import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const other = await browser.newPage();
  const url = `http://127.0.0.1:${process.argv[2]}`;
  await Promise.all([page.goto(url), other.goto(url)]);
  await Promise.all([page.waitForSelector('[data-row="alpha"]'), other.waitForSelector('[data-row="alpha"]')]);
  assert.deepEqual(await page.locator('[data-row]').evaluateAll(nodes => nodes.map(node => node.dataset.row)), ['alpha', 'beta']);
  assert.equal(await page.locator('[data-child]').count(), 4);
  console.log("  ok  nested lexical rows render in both sessions");
  await page.locator('#reverse').click();
  await page.waitForFunction(() => document.querySelector('[data-row]')?.dataset.row === 'beta');
  await page.locator('[data-pick="alpha"]').click();
  await page.waitForFunction(() => document.querySelector('#selected')?.textContent === 'alpha');
  assert.equal(await other.locator('#selected').textContent(), 'none');
  assert.deepEqual(await other.locator('[data-row]').evaluateAll(nodes => nodes.map(node => node.dataset.row)), ['alpha', 'beta']);
  console.log("  ok  reordered handlers capture their row and isolate sessions");
  await page.locator('#toggle').click();
  await page.waitForFunction(() => document.querySelector('#branch')?.textContent === 'second');
  assert.equal(await page.locator('#case').textContent(), 'pair');
  await page.locator('#toggle').click();
  await page.waitForFunction(() => document.querySelector('#case')?.textContent === 'other');
  assert.equal(await page.locator('#branch').textContent(), 'last');
  await page.locator('#remove').click();
  await page.waitForFunction(() => document.querySelectorAll('[data-row]').length === 1);
  assert.equal(await page.locator('[data-child]').count(), 2);
  console.log("  ok  branch alternatives and removed nested owners update");
  console.log("CONTROL BROWSER PASSED");
} finally { await browser.close(); }
