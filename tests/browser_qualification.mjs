import assert from "node:assert/strict";
import playwright from "playwright";

const engine = process.env.PYSX_BROWSER_ENGINE;
const browser = await playwright[engine].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    const Socket = window.WebSocket;
    window.ops = [];
    window.WebSocket = class extends Socket {
      constructor(...args) {
        super(...args);
        this.addEventListener("message", event => {
          const frame = JSON.parse(event.data);
          if (frame.ops) window.ops.push(...frame.ops);
        });
      }
    };
  });
  await page.goto(`http://127.0.0.1:${process.argv[2]}`);
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  assert.equal(await page.locator("[data-row]").count(), 1000);
  await page.evaluate(() => {
    window.savedRows = [...document.querySelectorAll("[data-row]")];
    window.savedFields = [...document.querySelectorAll("[data-field]")];
    window.ops = [];
  });
  const samples = [];
  for (let index = 0; index < 5; index++) {
    const started = performance.now();
    await page.locator('[data-field="500"]').fill(`edited-${index}`);
    await page.waitForFunction(value => document.querySelector('[data-row="500"] span').textContent === value, `edited-${index}`);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    samples.push((performance.now() - started) / 1000);
  }
  assert(await page.evaluate(() => window.ops.every(op => ["attr", "text"].includes(op.op))));
  console.log("  ok  five granular edits in the 1,000-row fixture");
  await page.locator('[data-field="500"]').evaluate(field => {
    field.focus(); field.setSelectionRange(2, 5, "backward"); window.ops = [];
  });
  await page.evaluate(() => document.getElementById("reverse").click());
  await page.waitForFunction(() => document.querySelector("[data-row]").dataset.row === "999");
  assert(await page.evaluate(() => window.savedRows.every(row => row === document.querySelector(`[data-row="${row.dataset.row}"]`)) &&
    window.savedFields.every(field => field === document.querySelector(`[data-field="${field.dataset.field}"]`))));
  assert.deepEqual(await page.locator('[data-field="500"]').evaluate(field => [document.activeElement === field, field.selectionStart, field.selectionEnd, field.selectionDirection]), [true, 2, 5, "backward"]);
  assert(await page.evaluate(() => window.ops.length === 1 && window.ops[0].op === "list" && Object.keys(window.ops[0].html).length === 0));
  console.log("  ok  all row/field identities, focus and selection survive reorder without replacements");
  assert.equal(await page.locator("table > tbody > tr > td").textContent(), "cell");
  assert.equal(await page.locator("select > option").count(), 1);
  assert.equal(await page.locator("svg circle").evaluate(node => node.namespaceURI), "http://www.w3.org/2000/svg");
  assert.equal(await page.locator("math mi").evaluate(node => node.namespaceURI), "http://www.w3.org/1998/Math/MathML");
  assert.deepEqual(errors, []);
  console.log("  ok  constrained table/select and SVG/MathML namespaces are valid");
  console.log("QUALIFICATION BROWSER SAMPLES: " + JSON.stringify({engine, version: browser.version(), rows: 1000, samples_seconds: samples,
    measurement: "automation fill through two animation callbacks; local observation, not reference paint qualification"}));
  console.log("QUALIFICATION BROWSER PASSED");
} finally { await browser.close(); }
