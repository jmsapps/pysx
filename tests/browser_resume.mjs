import assert from "node:assert/strict";
import playwright from "playwright";
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    const Original = WebSocket;
    window.WebSocket = class extends Original {
      constructor(...args) { super(...args); window.testSocket = this; }
    };
  });
  await page.goto(`http://127.0.0.1:${process.argv[2]}/`);
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "true");
  await page.evaluate(() => {
    window.savedField = document.querySelector("#resume-field");
    window.savedRow = document.querySelector("#resume-row-3");
    window.savedField.focus(); window.savedField.setSelectionRange(2, 6);
    window.testSocket.close();
  });
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "false");
  await page.waitForTimeout(1800);
  await page.evaluate(() => window.dispatchEvent(new Event("pysx:reconnect")));
  await page.waitForFunction(() => document.querySelector("#resume-row-4")?.textContent === "current");
  assert.equal(await page.evaluate(() => window.savedField === document.querySelector("#resume-field") &&
    window.savedRow === document.querySelector("#resume-row-3") && document.activeElement === window.savedField &&
    window.savedField.selectionStart === 2 && window.savedField.selectionEnd === 6), true);
  assert.deepEqual(await page.locator("p[id^=resume-row]").evaluateAll(nodes => nodes.map(node => [node.id, node.title])),
    [["resume-row-3", "current"], ["resume-row-4", "current"], ["resume-row-1", "current"]]);
  assert.deepEqual(errors, []);
  console.log("  ok  detached updates resume complete current keyed state without replacing focus or selection");
  console.log("CURRENT STATE BROWSER PASSED");
} finally { await browser.close(); }
