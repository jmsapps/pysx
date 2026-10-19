import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const base = `http://127.0.0.1:${process.argv[2]}`;
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  const other = await context.newPage();
  const errors = [];
  for (const target of [page, other]) target.on("pageerror", error => errors.push(error.message));
  const connected = target => target.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  const edit = action => page.request.get(base + "/__fixture__/edit?action=" + action);
  const count = (target, value) => target.waitForFunction(value => document.getElementById("count").textContent === String(value), value);
  const label = (target, value) => target.waitForFunction(value => document.getElementById("label").textContent === value, value);
  const status = async () => (await page.request.get(base + "/__pysx_dev__/status")).json();
  const metrics = async () => (await page.request.get(base + "/__fixture__/metrics")).json();
  await page.goto(base); await other.goto(base);
  await connected(page); await connected(other);
  await page.locator("#increment").click(); await count(page, 1);
  for (let i = 0; i < 3; i++) await other.locator("#increment").click();
  await count(other, 3);
  await page.locator("#field").fill("first draft");
  await other.locator("#field").fill("second draft");
  await page.waitForFunction(() => document.getElementById("echo").textContent === "first draft");
  await other.waitForFunction(() => document.getElementById("echo").textContent === "second draft");
  await page.locator("#field").focus();
  await page.locator("#uncommitted-check").check();
  await page.locator("#uncommitted-choice").selectOption("b");
  await page.locator("#field").focus();
  await page.evaluate(() => { window.retainedField = document.getElementById("field"); });
  const before = await metrics();
  await edit("updated");
  await label(page, "updated"); await label(other, "updated");
  await connected(page); await connected(other);
  await count(page, 1); await count(other, 3);
  assert.equal(await page.locator("#field").inputValue(), "first draft");
  assert.equal(await other.locator("#field").inputValue(), "second draft");
  assert.equal(await page.evaluate(() => window.retainedField === document.getElementById("field")), true);
  assert.equal(await page.locator("#field").evaluate(el => el === document.activeElement), true);
  assert.equal(await page.locator("#field").evaluate(el => el.defaultValue), "first draft");
  assert.equal(await page.locator("#uncommitted-check").isChecked(), false);
  assert.equal(await page.locator("#uncommitted-choice").inputValue(), "a");
  await page.locator("#increment").click(); await count(page, 3);
  const after = await metrics();
  assert.equal(after[".effects"] - before[".effects"], 2);
  assert.equal(after[".starts"] - after[".stops"], 2);
  console.log("  ok  distinct session state, DOM and focus survive; new handlers and exactly owned resources replace old ones");

  for (const [action, message] of [["bad_state", "state render failed"], ["break", "broken hot import"]]) {
    const generation = (await status()).generation;
    await edit(action);
    await page.waitForFunction(message => document.getElementById("pysx-dev-error")?.textContent.includes(message), message);
    assert.equal((await status()).generation, generation);
    await page.locator("#increment").click();
  }
  await count(page, 7);
  await edit("repaired"); await label(page, "repaired"); await label(other, "repaired");
  await connected(page); await connected(other); await count(page, 7); await count(other, 3);
  console.log("  ok  state-dependent render and mutating failed import preserve old globals, handlers and repairability");

  await edit("css");
  await page.waitForFunction(() => getComputedStyle(document.querySelector("main")).color === "rgb(0, 0, 255)");
  await count(page, 7); await count(other, 3);
  await edit("template");
  await page.waitForFunction(() => document.getElementById("note").textContent === "template two");
  await count(page, 7); await connected(page);
  await edit("slow");
  await page.locator("#field").fill("edited while candidate imports");
  await page.locator("#increment").click();
  await label(page, "slow"); await connected(page); await count(page, 9);
  assert.equal(await page.locator("#field").inputValue(), "edited while candidate imports");
  for (let cycle = 0; cycle < 3; cycle++) {
    await edit("cycle" + cycle); await label(page, "cycle" + cycle); await connected(page);
    await count(page, 9);
    assert.equal((await status()).workers.length, 1);
  }
  console.log("  ok  CSS/templates, in-flight edits and repeated reloads preserve state with one worker");

  await edit("incompatible");
  await count(page, 0); await count(other, 0); await connected(page);
  assert.equal(await page.locator("#field").inputValue(), "initial");
  await edit("original"); await label(page, "original"); await label(other, "original");
  await connected(page); await connected(other);
  assert.deepEqual(errors, []);
  console.log("  ok  incompatible schema follows the declared fresh-session reload fallback");
  console.log("STATE PRESERVING BROWSER PASSED");
} finally { await browser.close(); }
