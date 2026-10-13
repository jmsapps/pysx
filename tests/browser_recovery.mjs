import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage(), other = await browser.newPage();
  const errors = [], frames = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("websocket", socket => socket.on("framereceived", frame => frames.push(JSON.parse(frame.payload))));
  const url = `http://127.0.0.1:${process.argv[2]}/`;
  await page.goto(url); await other.goto(url);
  await page.waitForSelector("#fail"); await other.waitForSelector("#fail");
  await page.evaluate(() => { window.healthyNode = document.getElementById("healthy-field"); });
  let mutations = 0;
  for (const mode of ["fail", "async-fail", "input-fail", "change-fail", "submit-fail", "key-fail", "list-fail", "computed-fail", "effect-fail", "render-fail"]) {
    const start = frames.length;
    if (mode === "input-fail") await page.locator(`#${mode}`).fill("edit");
    else if (mode === "change-fail") await page.locator(`#${mode}`).selectOption("b");
    else if (mode === "key-fail") await page.locator(`#${mode}`).press("Enter");
    else await page.locator(`#${mode}`).click();
    await page.waitForSelector("#fallback");
    await page.waitForFunction(previous => Number(document.getElementById("mutations").textContent) > previous, mutations);
    mutations = Number(await page.locator("#mutations").textContent());
    const result = frames.slice(start).find(frame => frame.t === "result");
    assert.equal(result?.status, "error");
    assert.ok(result.error.owner);
    assert.ok(!JSON.stringify(result).includes("PRIVATE_EXCEPTION_TEXT"));
    assert.equal(await other.locator("#mutations").textContent(), "0");
    await page.locator("#healthy").click();
    await page.locator("#healthy-field").focus();
    await page.evaluate(() => document.getElementById("retry").click());
    await page.waitForSelector("#fail");
    assert.equal(await page.locator("#fallback").count(), 0);
    assert.equal(await page.evaluate(() => window.healthyNode === document.getElementById("healthy-field")), true);
    assert.equal(await page.locator("#healthy-field").evaluate(node => node === document.activeElement), true);
    console.log(`  ok  ${mode} commits state, contains locally, retries and preserves sibling focus and another session`);
  }
  assert.deepEqual(errors, []);
  console.log("LOCAL FAILURE BROWSER PASSED");
} finally { await browser.close(); }
