import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const other = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const address = `http://127.0.0.1:${process.argv[2]}`;
  await Promise.all([page.goto(address), other.goto(address)]);
  await Promise.all([page.waitForSelector("#sample"), other.waitForSelector("#sample")]);
  const computed = (target) => target.locator("#sample").evaluate(node => {
    const css = getComputedStyle(node); return [css.color, css.paddingLeft];
  });
  assert.deepEqual(await computed(page), ["rgb(0, 128, 0)", "3px"]);
  console.log("  ok  initial theme, literal rule and reactive variable snapshot");
  await page.locator("#change").click();
  await page.waitForFunction(() => getComputedStyle(document.querySelector("#sample")).paddingLeft === "7px");
  assert.deepEqual(await computed(page), ["rgb(0, 0, 255)", "7px"]);
  assert.equal(await page.locator("#sample").evaluate(node => node.style.getPropertyValue("--local")), "");
  assert.equal(await page.locator("#sample").getAttribute("css"), null);
  assert.match(await page.locator("#sample").getAttribute("class"), /dynamic.*pysx-|pysx-.*dynamic/);
  console.log("  ok  theme switch, empty variable removal and merged dynamic class");
  assert.deepEqual(await computed(other), ["rgb(0, 128, 0)", "3px"]);
  console.log("  ok  independent websocket sessions have isolated styles and themes");
  await page.locator("#toggle").click();
  await page.waitForFunction(() => !document.querySelector("#owned"));
  assert.equal(await page.locator("style").evaluate(node => node.textContent.includes("37px")), false);
  await page.locator("#toggle").click();
  await page.waitForSelector("#owned");
  assert.equal(await page.locator("#owned").evaluate(node => getComputedStyle(node).marginLeft), "37px");
  console.log("  ok  branch rule releases and restores with the owning DOM");
  await page.locator("#clear").click();
  await page.waitForFunction(() => document.querySelector("style").textContent.includes("--ink:unset"));
  assert.equal(errors.length, 0);
  console.log("  ok  clear resets named root variables without browser errors");
  console.log("STYLES BROWSER PASSED");
} finally { await browser.close(); }
