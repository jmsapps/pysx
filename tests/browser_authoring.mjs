import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
  const other = await browser.newPage();
  const url = `http://127.0.0.1:${process.argv[2]}`;
  await Promise.all([page.goto(url), other.goto(url)]);
  await Promise.all([page.waitForSelector("#sample"), other.waitForSelector("#sample")]);
  const value = (target, property) => target.locator("#sample").evaluate((node, p) => getComputedStyle(node)[p], property);
  assert.equal(await value(page, "color"), "rgb(255, 0, 0)");
  await page.locator("#sample").evaluate(node => { window.originalNode = node; });
  await page.locator("#sample").hover();
  assert.equal(await value(page, "borderTopColor"), "rgb(0, 128, 0)");
  console.log("  ok  nested hover and attribute selectors are scoped");
  await page.keyboard.press("Tab");
  await page.locator("#sample").focus();
  await page.keyboard.press("ArrowRight");
  assert.equal(await value(page, "outlineColor"), "rgb(128, 0, 128)");
  await page.mouse.down();
  assert.equal(await value(page, "borderTopWidth"), "5px");
  await page.mouse.up();
  await page.waitForFunction(() => getComputedStyle(document.querySelector("#sample")).backgroundColor === "rgb(0, 0, 0)");
  assert.equal(await page.locator("#sample").getAttribute("variant"), null);
  assert.equal(await page.evaluate(() => window.originalNode === document.querySelector("#sample")), true);
  assert.equal(await value(page, "color"), "rgb(0, 0, 255)");
  assert.equal(await value(other, "color"), "rgb(255, 0, 0)");
  assert.equal(await value(other, "backgroundColor"), "rgb(255, 255, 255)");
  console.log("  ok  keyboard focus, active state and variants preserve nodes and isolate sessions");
  await page.setViewportSize({ width: 500, height: 700 });
  assert.equal(await value(page, "paddingLeft"), "12px");
  assert.equal(await page.locator("section span").evaluate(node => getComputedStyle(node).color), "rgb(0, 128, 0)");
  assert.notEqual(await page.locator("#outside").evaluate(node => getComputedStyle(node).color), "rgb(0, 128, 0)");
  console.log("  ok  media queries and descendants stay within the component");
  console.log("AUTHORING BROWSER PASSED");
} finally { await browser.close(); }
