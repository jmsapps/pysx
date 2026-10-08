import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const other = await browser.newPage();
  const url = `http://127.0.0.1:${process.argv[2]}`;
  await Promise.all([page.goto(url), other.goto(url)]);
  await Promise.all([page.waitForSelector('#templates-example'), other.waitForSelector('#templates-example')]);
  const snapshots = await page.locator('#snapshots').textContent();
  const siblings = await page.evaluateHandle(() => {
    const parent = document.querySelector('#inline-siblings');
    const heading = document.querySelector('#live-heading');
    const [first, second] = parent.querySelectorAll(':scope > br');
    return { parent, heading, first, second };
  });
  const sameSiblings = () => page.evaluate(({parent, heading, first, second}) =>
    heading === document.querySelector('#live-heading') &&
    first === parent.querySelectorAll(':scope > br')[0] &&
    second === parent.querySelectorAll(':scope > br')[1] &&
    heading.parentNode === parent && first.parentNode === parent && second.parentNode === parent &&
    heading.nextElementSibling === first && first.nextElementSibling === second &&
    second.nextElementSibling === document.querySelector('#advance-heading'), siblings);
  assert(await sameSiblings());
  await page.locator('#advance-heading').click();
  await page.waitForFunction(() => document.querySelector('#live-heading')?.textContent === 'Live values updated');
  assert(await sameSiblings(), 'heading and both breaks retain their DOM objects and parent');
  assert.equal(await other.locator('#live-heading').textContent(), 'Live values');
  await other.locator('#advance-heading').click();
  await other.waitForFunction(() => document.querySelector('#live-heading')?.textContent === 'Live values updated');
  await page.locator('#advance-heading').click();
  await page.waitForFunction(() => document.querySelector('#live-heading')?.textContent === 'Live values');
  assert.equal(await other.locator('#live-heading').textContent(), 'Live values updated');
  assert(await sameSiblings());
  await siblings.dispose();
  console.log("  ok  inline heading and styled breaks preserve sibling identity and isolate later handlers");
  assert.equal(await page.locator('[data-group]').count(), 2);
  assert.equal(await page.locator('[data-pick]').count(), 3);
  assert.equal(await page.locator('[data-pick="work:plan"]').getAttribute('title'), null);
  assert.equal(await page.locator('[data-pick="work:build"]').getAttribute('title'), 'Build something');
  console.log("  ok  indexed nested lexical rows and conditional attrs render");
  await page.locator('#reverse').click();
  await page.waitForFunction(() => document.querySelector('[data-group]')?.dataset.group === 'learn');
  assert.equal(await page.locator('[data-group="learn"] h2').textContent(), '1. Learn');
  await page.locator('#add-child').click();
  await page.waitForSelector('[data-pick="learn:child-2"]');
  assert.equal(await other.locator('[data-pick="learn:child-2"]').count(), 0);
  await page.locator('[data-pick="work:build"]').click();
  await page.waitForFunction(() => document.querySelector('#selected')?.textContent === 'Selected: work / build');
  assert.equal(await other.locator('#selected').textContent(), 'Selected: none');
  console.log("  ok  captured handlers survive reorder and isolate sessions");
  await page.locator('#cycle').click();
  await page.waitForFunction(() => document.querySelector('#branch')?.textContent === 'Compact details');
  await page.locator('#cycle').click();
  await page.waitForFunction(() => document.querySelector('#case')?.textContent === 'Quiet mode');
  await page.locator('#add').click();
  await page.waitForSelector('[data-group="extra-1"]');
  await page.locator('#remove').click();
  await page.waitForFunction(() => !document.querySelector('[data-group="extra-1"]'));
  assert.equal(await page.locator('#snapshots').textContent(), snapshots);
  assert.equal(await other.locator('#branch').textContent(), 'Full details');
  console.log("  ok  branches, row cleanup and fixed snapshots coexist");
  console.log("TEMPLATES BROWSER PASSED");
} finally { await browser.close(); }
