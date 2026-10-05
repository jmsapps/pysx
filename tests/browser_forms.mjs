import assert from "node:assert/strict";
import playwright from "playwright";

const engine = process.env.PYSX_BROWSER_ENGINE;
if (!["chromium", "firefox", "webkit"].includes(engine)) throw new Error("missing engine");
if (process.platform === "darwin") delete process.env.DYLD_LIBRARY_PATH;
const browser = await playwright[engine].launch();
const waitText = (page, id, text) => page.waitForFunction(
  ([id, text]) => document.getElementById(id).textContent === text, [id, text]);
try {
  const page = await browser.newPage();
  const other = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    const original = document.addEventListener;
    window.controlListeners = {};
    document.addEventListener = function(type, listener, options) {
      window.controlListeners[type] = (window.controlListeners[type] ?? 0) + 1;
      return original.call(this, type, listener, options);
    };
    const nativeSend = WebSocket.prototype.send;
    window.sentEdits = [];
    WebSocket.prototype.send = function(data) {
      window.sentEdits.push(JSON.parse(data));
      return nativeSend.call(this, data);
    };
    const Socket = window.WebSocket;
    window.WebSocket = class extends Socket {
      constructor(...args) {
        super(...args); window.liveSocket = this;
      }
    };
  });
  const address = `http://127.0.0.1:${Number(process.argv[2])}/`;
  await Promise.all([page.goto(address), other.goto(address)]);
  await Promise.all([page.waitForSelector("#text"), other.waitForSelector("#text")]);
  assert.equal(await page.locator("#note").inputValue(), "note");
  assert.deepEqual(await page.locator("#multi").evaluate(el => [...el.selectedOptions].map(o => o.value)), ["a"]);
  await page.locator("#text").fill("typed");
  await waitText(page, "text-state", "typed");
  await page.locator("#note").fill("new note");
  await waitText(page, "note-state", "new note");
  await page.locator("#check").check();
  await waitText(page, "check-state", "True");
  await page.locator("#single").selectOption("b");
  await waitText(page, "single-state", "b");
  await page.locator("#multi").selectOption(["a", "b"]);
  await waitText(page, "multi-state", "['a', 'b']");
  await page.locator("#radio-b").check();
  await waitText(page, "radio-state", "b");
  assert.equal(await other.locator("#text").inputValue(), "initial");
  assert.equal(await other.locator("#check").isChecked(), false);
  console.log("  ok  text, checked, textarea, single/multiple select and radio edits isolate sessions");
  await page.locator("#program").click();
  await waitText(page, "text-state", "server");
  assert.equal(await page.locator("#text").inputValue(), "server");
  assert.equal(await page.locator("#note").inputValue(), "server note");
  assert.equal(await page.locator("#single").inputValue(), "b");
  assert.deepEqual(await page.locator("#multi").evaluate(el => [...el.selectedOptions].map(o => o.value)), ["b"]);
  assert.equal(await page.locator("#radio-b").isChecked(), true);
  console.log("  ok  authoritative server writes update dirty controls and structured fields");
  await page.locator("#corrected").evaluate(el => {
    el.focus(); el.value = "abc"; el.setSelectionRange(1, 1);
    el.dispatchEvent(new InputEvent("input", { bubbles: true }));
  });
  await page.waitForFunction(() => document.getElementById("corrected").value === "ABC");
  assert.equal(await page.locator("#corrected").evaluate(el => el.selectionStart), 1);
  await page.locator("#corrected").evaluate(el => {
    el.value = "AbC"; el.setSelectionRange(2, 2);
    el.dispatchEvent(new InputEvent("input", { bubbles: true }));
  });
  await page.waitForFunction(() => document.getElementById("corrected").value === "ABC");
  assert.equal(await page.locator("#corrected").evaluate(el => el.selectionStart), 2);
  console.log("  ok  normalization corrects unchanged server values while preserving the caret");
  const before = await page.evaluate(() => window.sentEdits.length);
  await page.locator("#text").evaluate(el => {
    el.focus();
    el.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    el.value = "draft";
    el.dispatchEvent(new InputEvent("input", { bubbles: true, isComposing: true }));
  });
  assert.equal(await page.locator("#text-state").textContent(), "server");
  assert.equal(await page.evaluate(() => window.sentEdits.length), before);
  await page.locator("#text").evaluate(el => {
    el.value = "完成";
    el.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true, data: "完成" }));
    el.dispatchEvent(new InputEvent("input", { bubbles: true }));
  });
  await waitText(page, "text-state", "完成");
  assert.equal(await page.evaluate(() => window.sentEdits.length), before + 1);
  console.log("  ok  composition holds intermediate edits and commits the final value once");
  await page.locator("#text").evaluate(el => {
    const edit = window.sentEdits.findLast(message => message.h === el.dataset.pysxBinding);
    window.liveSocket.onmessage({ data: JSON.stringify({ t: "patch", ops: [{
      op: "attr", id: el.dataset.pysxEl, name: "value", v: "stale", rev: edit.rev - 1,
    }] }) });
  });
  assert.equal(await page.locator("#text").inputValue(), "完成");
  console.log("  ok  stale authoritative replies cannot overwrite newer edits");
  await page.locator("#text").fill("");
  await waitText(page, "text-state", "");
  await page.locator("#submit").click();
  await waitText(page, "invalid-state", "1");
  assert.equal(await page.locator("#status").textContent(), "ready");
  await page.locator("#text").fill("valid");
  await waitText(page, "text-state", "valid");
  await page.locator("#submit").click();
  await page.waitForFunction(() => document.getElementById("status").textContent !== "ready");
  const submitted = JSON.parse(await page.locator("#status").textContent());
  assert.deepEqual(submitted.entries, [
    ["text", "valid"], ["note", "server note"], ["check", "yes"], ["single", "b"],
    ["color", "b"], ["radio", "b"], ["action", "save"],
  ]);
  assert.equal(submitted.valid, true);
  assert.deepEqual(submitted.submitter, { name: "action", value: "save" });
  console.log("  ok  validation blocks submit and successful controls include submitter, omit disabled");
  await page.locator("#reset").click();
  await waitText(page, "reset-state", "1");
  assert.equal(await page.locator("#text").inputValue(), "initial");
  assert.equal(await page.locator("#text-state").textContent(), "initial");
  assert.equal(await page.locator("#note").inputValue(), "note");
  assert.equal(await page.locator("#check").isChecked(), false);
  assert.equal(await page.locator("#check-state").textContent(), "False");
  assert.equal(await page.locator("#single").inputValue(), "a");
  assert.deepEqual(await page.locator("#multi").evaluate(el => [...el.selectedOptions].map(o => o.value)), ["a"]);
  assert.equal(await page.locator("#radio-a").isChecked(), true);
  console.log("  ok  native reset restores initial defaults and bound state in one transaction");
  const listenerCount = await page.evaluate(() => window.controlListeners);
  await page.locator("#owned").evaluate(el => { window.savedOwned = el; });
  await page.locator("#owned").fill("edit owned");
  assert.equal(await page.locator("#owned").evaluate(el => el === window.savedOwned), true);
  for (let i = 0; i < 5; i++) {
    await page.locator("#toggle").click();
    await page.waitForFunction(() => !document.getElementById("owned"));
    await page.locator("#toggle").click();
    await page.waitForSelector("#owned");
  }
  assert.deepEqual(await page.evaluate(() => window.controlListeners), listenerCount);
  assert.deepEqual(errors, []);
  console.log("  ok  conditional edits retain identity and repeated ownership changes keep listener counts bounded");
  await Promise.all([page.close(), other.close()]);
  console.log("FORMS BROWSER PASSED");
} finally {
  await browser.close();
}
