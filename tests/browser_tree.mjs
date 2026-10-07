import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${process.argv[2]}`);
  const focused = async id => page.waitForFunction(id => document.activeElement?.id === id, id);
  await page.waitForFunction(() => document.querySelector("#tree-mounts")?.textContent.endsWith("2"));
  assert.equal(await page.locator("#composition-tree").getAttribute("role"), "tree");
  assert.equal(await page.locator("#tree-library").getAttribute("aria-expanded"), "false");
  assert.equal(await page.locator("#tree-notes").getAttribute("aria-expanded"), null);
  assert.equal(await page.locator("#composition-tree").locator('[tabindex="0"]').count(), 1);
  const panel = await page.locator("#composition-tree").evaluate(node => {
    const css = getComputedStyle(node.parentElement);
    return [css.paddingTop, css.borderTopWidth, css.backgroundColor];
  });
  assert.deepEqual(panel, ["16px", "1px", "rgb(255, 255, 255)"]);
  console.log("  ok  recursive callable panel exposes tree roles and one tab stop");
  await page.keyboard.press("Tab");
  await focused("tree-library");
  await page.locator("#tree-library > span").click();
  await page.waitForSelector("#tree-components");
  await focused("tree-library");
  await page.keyboard.press("ArrowDown");
  await focused("tree-components");
  await page.keyboard.press("ArrowRight");
  await page.waitForSelector("#tree-signals");
  await page.keyboard.press("ArrowRight");
  await focused("tree-signals");
  assert.equal(await page.locator("#tree-signals").getAttribute("aria-level"), "3");
  console.log("  ok  click retains keyboard focus and arrow keys reach recursive descendants");
  await page.keyboard.press("ArrowLeft");
  await focused("tree-components");
  await page.keyboard.press("ArrowLeft");
  await page.waitForFunction(() => !document.querySelector("#tree-signals"));
  await focused("tree-components");
  await page.keyboard.press("End");
  await focused("tree-notes");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.querySelector("#tree-notes").textContent.includes("activations: 1"));
  const original = await page.locator("#tree-notes").elementHandle();
  await page.locator("#tree-reverse").click();
  await page.waitForFunction(() => document.querySelector('#composition-tree > pysx-list > li').id === "tree-notes");
  assert.equal(await original.evaluate(node => node === document.querySelector("#tree-notes")), true);
  assert.ok((await page.locator("#tree-notes").textContent()).includes("activations: 1"));
  console.log("  ok  root reorder preserves component state, handlers and untouched DOM");
  // Traverse through the widget's keyboard handler so its owned focus command
  // follows the tab-stop patches. A raw focus() on a different row races those
  // server-driven row patches, especially in WebKit.
  await page.locator("#tree-reverse").focus();
  await page.keyboard.press("Shift+Tab");
  await focused("tree-notes");
  await page.keyboard.press("ArrowDown");
  await focused("tree-library");
  await page.keyboard.press("ArrowLeft");
  await page.waitForFunction(() => !document.querySelector("#tree-components"));
  await focused("tree-library");
  assert.equal(await page.locator("#tree-library").getAttribute("aria-expanded"), "false");
  assert.ok((await page.locator("#tree-cleanups").textContent()).endsWith("3"));
  assert.deepEqual(errors, []);
  console.log("  ok  collapse cleans descendants and preserves focus with correct ARIA");
  console.log("TREE BROWSER PASSED");
} finally { await browser.close(); }
