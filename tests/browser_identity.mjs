import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${process.argv[2]}`);
  await page.waitForSelector("#rows > tr");
  assert.equal(await page.locator("#rows > tr > td").count(), 2);
  assert.equal(await page.locator("#options > option").count(), 2);
  assert.equal(await page.locator("#fragments > input").count(), 2);
  assert.equal(await page.locator("#fragments > x-row").count(), 2);
  assert.equal(await page.locator("#drawing > g").evaluateAll(nodes => nodes.every(node => node.namespaceURI === "http://www.w3.org/2000/svg")), true);
  assert.equal(await page.locator("#formula > mi").evaluateAll(nodes => nodes.every(node => node.namespaceURI === "http://www.w3.org/1998/Math/MathML")), true);
  assert.equal(await page.locator("#rows pysx-slot, #options pysx-slot, #drawing pysx-slot, #formula pysx-slot").count(), 0);
  console.log("  ok  legal table/select/foreign/void/custom/multiple-root DOM");
  await page.evaluate(() => { window.original = document.querySelector('#rows > tr'); window.control = document.querySelector('#fragments > input'); });
  await page.locator("#reverse").click();
  await page.waitForFunction(() => document.querySelector('#rows > tr')?.dataset.row !== 'alpha');
  assert.equal(await page.evaluate(() => window.original === document.querySelector('#rows > tr:last-of-type') && window.control === document.querySelector('#fragments > input:last-of-type')), true);
  assert.equal(await page.locator("#fragments > span").evaluateAll(nodes => nodes.map(node => node.dataset.part)).then(keys => keys[0]), 'quotes"{λ}-->:');
  console.log("  ok  encoded keys and multiple-root ranges retain nodes on reorder");
  await page.locator("#add").click();
  await page.waitForFunction(() => document.querySelectorAll('#rows > tr').length === 3);
  assert.equal(await page.locator("#live").textContent(), "updated");
  await page.waitForSelector("#branch-cell");
  assert.equal(await page.locator("#branch-cell").textContent(), "updated");
  assert.equal(await page.locator("#branch-rows pysx-slot").count(), 0);
  assert.equal(await page.locator("#drawing > g:last-of-type").evaluate(node => node.namespaceURI), "http://www.w3.org/2000/svg");
  assert.equal(await page.locator("#formula > mi:last-of-type").evaluate(node => node.namespaceURI), "http://www.w3.org/1998/Math/MathML");
  await page.locator("#remove").click();
  await page.waitForFunction(() => document.querySelectorAll('#options > option').length === 2);
  assert.equal(await page.locator("#fragments > input").count(), 2);
  assert.deepEqual(errors, []);
  console.log("  ok  contextual insertion, removal and live comment slots");
  console.log("IDENTITY BROWSER PASSED");
} finally { await browser.close(); }
