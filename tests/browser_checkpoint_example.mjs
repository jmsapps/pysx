import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    const Original = WebSocket;
    window.WebSocket = class extends Original {
      constructor(...args) { super(...args); window.exampleSocket = this; }
    };
  });
  const url = `http://127.0.0.1:${process.argv[2]}/`;
  await page.goto(url);
  const attached = () => page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "true");
  await attached();
  await page.locator("#recovery-name").fill("Mira");
  await page.locator("#recovery-notes").fill("line one\nline two");
  await page.click("#add-revision");
  await page.waitForFunction(() => document.getElementById("recovery-revision").textContent === "1");
  await page.locator("#recovery-name").evaluate(input => {
    window.retainedName = input; input.focus(); input.setSelectionRange(1, 3);
    window.exampleSocket.close();
  });
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "false");
  await attached();
  assert.equal(await page.locator("#recovery-name").inputValue(), "Mira");
  assert.equal(await page.locator("#recovery-notes").inputValue(), "line one\nline two");
  assert.equal(await page.evaluate(() => window.retainedName === document.getElementById("recovery-name") &&
    document.activeElement === window.retainedName && window.retainedName.selectionStart === 1 &&
    window.retainedName.selectionEnd === 3), true);
  const other = await context.newPage();
  await other.goto(url);
  await other.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "true");
  assert.equal(await other.locator("#recovery-name").inputValue(), "");
  await page.reload();
  await attached();
  assert.equal(await page.locator("#recovery-name").inputValue(), "");
  assert.equal(await page.locator("#recovery-revision").textContent(), "0");
  assert.deepEqual(errors, []);
  console.log("  ok  ordinary shared-component form edits and revisions survive live reconnect with focus");
  console.log("  ok  same-cookie tabs and page refresh start independent drafts");
  console.log("BROWSER RECOVERY EXAMPLE PASSED");
} finally { await browser.close(); }
