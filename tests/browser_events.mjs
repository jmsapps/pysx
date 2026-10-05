import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${Number(process.argv[2])}/`);
  await page.waitForSelector("#keys");
  await page.evaluate(() => {
    const original = WebSocket.prototype.send;
    WebSocket.prototype.send = function(data) { setTimeout(() => original.call(this, data), 150); };
  });
  assert.equal(await page.locator("#keys").evaluate(el => el.dispatchEvent(
    new KeyboardEvent("keydown", {key: "ArrowDown", code: "ArrowDown", shiftKey: true, bubbles: true, cancelable: true}))), false);
  await page.waitForFunction(() => document.getElementById("event-result").textContent.includes("keydown:ArrowDown:True:keys"));
  assert.equal(await page.locator("#event-count").textContent(), "1");
  assert.equal(await page.locator("#keys").evaluate(el => el.dispatchEvent(
    new KeyboardEvent("keydown", {key: "Tab", bubbles: true, cancelable: true}))), true);
  console.log("  ok  key filters and cancellation happen before delayed delivery, once");
  await page.locator("#child").click();
  await page.waitForFunction(() => document.getElementById("event-result").textContent.includes("child-click;"));
  assert.equal((await page.locator("#event-result").textContent()).includes("parent-click"), false);
  console.log("  ok  nested bubble propagation stops at the declared owner");
  await page.locator("#pointer").click();
  await page.waitForFunction(() => document.getElementById("event-result").textContent.includes("capture-pointerdown;bubble-pointerdown;"));
  console.log("  ok  pointer capture precedes descendant bubble dispatch");
  await page.locator("#custom").evaluate(el => el.dispatchEvent(new CustomEvent("custom-ready", {bubbles: true})));
  await page.waitForFunction(() => document.getElementById("event-result").textContent.startsWith("custom-ready:"));
  assert.equal(await page.locator("#link").evaluate(el => el.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true}))), false);
  for (const id of ["link", "hash-link", "download-link", "target-link"]) {
    assert.equal(await page.locator(`#${id}`).evaluate(el => {
      const event = new MouseEvent("click", {bubbles: true, cancelable: true, ctrlKey: el.id === "link"});
      // Suppress native navigation separately after observing the declared policy.
      let prevented;
      const observe = e => { prevented = e.defaultPrevented; e.preventDefault(); };
      window.addEventListener("click", observe, {once: true});
      el.dispatchEvent(event);
      return prevented;
    }), false);
  }
  await page.locator("#submit").click();
  await page.waitForFunction(() => document.getElementById("event-result").textContent.startsWith("submit:"));
  console.log("  ok  custom events, eligible links and submit policies preserve native modifiers");
  const before = Number(await page.locator("#event-count").textContent());
  await page.locator("#bound").evaluate(el => {
    el.dispatchEvent(new CompositionEvent("compositionstart", {bubbles: true}));
    el.value = "composed";
    el.dispatchEvent(new InputEvent("input", {bubbles: true, isComposing: true}));
    el.dispatchEvent(new CompositionEvent("compositionend", {bubbles: true}));
    el.dispatchEvent(new InputEvent("input", {bubbles: true}));
  });
  await page.waitForFunction(() => document.getElementById("bound-value").textContent === "composed");
  await page.waitForFunction(() => document.getElementById("event-result").textContent === "input::False:bound");
  assert.equal(Number(await page.locator("#event-count").textContent()), before + 1);
  await page.locator("#bound").fill("edited");
  await page.waitForFunction(() => document.getElementById("bound-value").textContent === "edited");
  await page.waitForFunction(count => Number(document.getElementById("event-count").textContent) === count, before + 2);
  console.log("  ok  composed binding commits produce one typed input callback after the edit");
  await page.locator("#reset-input").fill("edited");
  await page.waitForFunction(() => document.getElementById("reset-value").textContent === "edited");
  await page.locator("#reset").click();
  await page.waitForFunction(() => document.getElementById("reset-value").textContent === "initial");
  await page.waitForFunction(() => document.getElementById("event-result").textContent.includes("reset:initial;"));
  console.log("  ok  a typed reset delivers resynchronised bindings before its callback");
  assert.deepEqual(errors, []);
  console.log("EVENTS BROWSER PASSED");
} finally { await browser.close(); }
