import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${process.argv[2]}`);
  const text = async (id, value) => page.waitForFunction(
    ([id, value]) => document.getElementById(id)?.textContent === value, [id, value]);
  await text("mounts", "2");
  await text("setups", "2");
  console.log("  ok  browser commit acknowledgements run mount hooks once");
  await page.locator("#a button").click();
  await page.waitForFunction(() => document.querySelector("#a button").textContent === "1");
  const original = await page.locator("#a").elementHandle();
  await page.locator("#reverse").click();
  await page.waitForFunction(() => document.querySelector("ul li").id === "b");
  assert.equal(await original.evaluate(node => node === document.querySelector("#a")), true);
  await text("setups", "2");
  await text("mounts", "2");
  console.log("  ok  keyed reorder preserves state and DOM identity without setup multiplication");
  await page.locator("#remove").click();
  await page.waitForFunction(() => !document.querySelector("#a"));
  await text("cleanups", "1");
  await page.locator("#restore").click();
  await text("mounts", "3");
  await text("setups", "3");
  assert.equal(await page.locator("#a button").textContent(), "0");
  assert.deepEqual(errors, []);
  console.log("  ok  removal cleans once and remount resets state with a fresh acknowledgement");
  console.log("COMPONENTS BROWSER PASSED");
} finally { await browser.close(); }
