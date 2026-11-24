import assert from "node:assert/strict";
import {mkdirSync, writeFileSync} from "node:fs";
import path from "node:path";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const pages = [];
const trace = [];
const errors = [];
try {
  const page = await browser.newPage();
  pages.push(page);
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${Number(process.argv[2])}/`);
  await page.waitForSelector("#combo");
  const press = async key => {
    trace.push({time: Date.now(), key, active: await page.evaluate(() => document.activeElement?.id)});
    await page.keyboard.press(key);
  };
  const waitAttr = (id, attr, value) => page.waitForFunction(({id, attr, value}) => document.getElementById(id).getAttribute(attr) === value, {id, attr, value});
  await page.locator("#combo").focus();
  await press("ArrowDown");
  await waitAttr("combo", "aria-activedescendant", "option-1");
  assert.equal(await page.locator("#combo").getAttribute("aria-expanded"), "true");
  assert.equal(await page.locator("#option-1").getAttribute("aria-selected"), "true");
  await press("ArrowUp"); await waitAttr("combo", "aria-activedescendant", "option-0");
  await press("Enter");
  await page.waitForFunction(() => document.getElementById("selected").textContent === "Red");
  assert.equal(await page.locator("#combo").inputValue(), "Red");
  await press("ArrowDown"); await waitAttr("combo", "aria-expanded", "true");
  await press("Escape"); await waitAttr("combo", "aria-expanded", "false");
  await press("ArrowDown"); await waitAttr("combo", "aria-expanded", "true");
  await page.locator("#option-2").click();
  await page.waitForFunction(() => document.getElementById("selected").textContent === "Green");
  assert.equal(await page.locator("#combo").evaluate(el => el === document.activeElement), true);
  console.log("  ok  arrows, Enter, Escape and pointer selection update combobox ARIA while retaining focus");
  await press("Tab");
  await page.waitForFunction(() => document.activeElement.id === "chip-0");
  await page.waitForFunction(() => document.getElementById("focused").textContent === "chip-0");
  // Acknowledge commands late, while the receiver continues to admit replies.
  await page.evaluate(() => {
    const original = WebSocket.prototype.send;
    WebSocket.prototype.send = function(data) {
      if (JSON.parse(data).t === "dom_reply") setTimeout(() => original.call(this, data), 100);
      else original.call(this, data);
    };
  });
  await press("ArrowRight");
  await page.waitForFunction(() => document.activeElement.id === "chip-1");
  await waitAttr("chip-1", "tabindex", "0");
  assert.equal(await page.locator("#chip-0").getAttribute("tabindex"), "-1");
  await press("ArrowLeft");
  await page.waitForFunction(() => document.activeElement.id === "chip-0");
  await press("Tab");
  await page.waitForFunction(() => document.activeElement.id === "after");
  await page.waitForFunction(() => document.getElementById("focused").textContent === "none");
  console.log("  ok  native Tab, roving focus and focus/blur events tolerate delayed command replies");
  await page.locator("#focus-combo").click();
  await page.waitForFunction(() => document.activeElement.id === "combo");
  const other = await browser.newPage();
  pages.push(other);
  await other.goto(`http://127.0.0.1:${Number(process.argv[2])}/`);
  await other.waitForSelector("#combo");
  assert.equal(await other.locator("#combo").inputValue(), "");
  assert.equal(await other.locator("#selected").textContent(), "None selected");
  await other.locator("#chip-1").click();
  await other.waitForFunction(() => document.getElementById("selected").textContent === "Blue");
  assert.equal(await other.locator("#combo").inputValue(), "Blue");
  assert.equal(await other.locator("#selected").evaluate(el =>
    getComputedStyle(el.parentElement.firstElementChild).backgroundColor), "rgb(59, 130, 246)");
  await other.close();
  console.log("  ok  explicit owned focus and browser session isolation");
  assert.deepEqual(errors, []);
  console.log("KEYBOARD BROWSER PASSED");
} catch (error) {
  const snapshots = await Promise.all(pages.map(page => page.evaluate(() => {
    const combo = document.getElementById("combo");
    return {active: document.activeElement?.id, focused: document.hasFocus(),
      visibility: document.visibilityState,
      connection: document.getElementById("pysx-root")?.dataset.pysxConnection,
      combo: combo && {visible: combo.checkVisibility?.() ?? null,
        expanded: combo.getAttribute("aria-expanded"),
        activedescendant: combo.getAttribute("aria-activedescendant"),
        value: combo.value},
      selected: document.getElementById("selected")?.textContent,
      focus: document.getElementById("focused")?.textContent};
  }).catch(failure => ({unavailable: failure.message}))));
  const diagnostic = JSON.stringify({engine: process.env.PYSX_BROWSER_ENGINE,
    error: error.message, pageErrors: errors, trace, snapshots});
  console.error(`PYSX KEYBOARD FAILURE: ${diagnostic}`);
  if (process.env.PYSX_DEPLOYMENT_LOGS) {
    try {
      mkdirSync(process.env.PYSX_DEPLOYMENT_LOGS, {recursive: true});
      writeFileSync(path.join(process.env.PYSX_DEPLOYMENT_LOGS,
        `keyboard-${process.env.PYSX_BROWSER_ENGINE}-${process.pid}-${Date.now()}.json`), diagnostic);
    } catch (failure) { console.error(`Could not preserve keyboard diagnostics: ${failure.message}`); }
  }
  throw error;
} finally { await browser.close(); }
