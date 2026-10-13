import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  let release;
  const delayed = new Promise(resolve => { release = resolve; });
  await page.route("**/client.js", async route => { await delayed; await route.continue(); });
  const load = page.goto(`http://127.0.0.1:${process.argv[2]}/deep-link`);
  await page.waitForSelector("#text");
  assert.equal(await page.title(), "HTTP attachment");
  assert.equal(await page.locator("#mounted").textContent(), "0");
  assert.equal(await page.locator("#setup").textContent(), "1");
  assert.equal(await page.locator("#choice").inputValue(), "b");
  assert.equal(await page.locator("#implicit").inputValue(), "B");
  assert.equal(await page.locator("#newline").inputValue(), "\nhello");
  assert.equal(await page.locator("#newline").evaluate(el => el.defaultValue), "\nhello");
  console.log("  ok  HTTP alone includes app content, head, setup state and native control defaults");
  await page.locator("#text").evaluate(el => {
    window.originalInput = el;
    el.value = "edited before attachment";
    document.getElementById("choice").value = "a";
    document.getElementById("check").checked = true;
    el.focus(); el.setSelectionRange(3, 3);
  });
  release(); await load;
  await page.waitForSelector('[data-pysx-attached="true"]');
  await page.waitForFunction(() => document.getElementById("mounted").textContent === "1");
  assert.equal(await page.locator("#text-state").textContent(), "edited before attachment");
  assert.equal(await page.locator("#note").inputValue(), "edited before attachment");
  assert.equal(await page.locator("#choice-state").textContent(), "a");
  assert.equal(await page.locator("#check-state").textContent(), "True");
  assert.equal(await page.evaluate(() => window.originalInput === document.getElementById("text")), true);
  assert.equal(await page.locator("#text").evaluate(el => el === document.activeElement && el.selectionStart === 3), true);
  assert.equal(await page.locator("#setup").textContent(), "1");
  console.log("  ok  graph adoption reconciles dirty shared bindings and preserves node, focus and caret");
  await page.locator("#text").fill("live edit");
  await page.waitForFunction(() => document.getElementById("text-state").textContent === "live edit");
  assert.deepEqual(errors, []);
  console.log("  ok  adopted handlers remain live and mount once");
  console.log("MEANINGFUL HTTP BROWSER PASSED");
} finally { await browser.close(); }
