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
  const label = async (target, value) => {
    try {
      await target.waitForFunction(value => document.getElementById("label").textContent === value, value);
    } catch (cause) {
      const document = await target.evaluate(() => ({
        url: location.href,
        label: document.getElementById("label")?.textContent,
        count: document.getElementById("count")?.textContent,
        connection: document.getElementById("pysx-root")?.dataset.pysxConnection,
        error: document.getElementById("pysx-dev-error")?.textContent,
      })).catch(error => ({ error: error.message }));
      const supervisor = await page.request.get(base + "/__pysx_dev__/status", { timeout: 3000 })
        .then(response => response.json()).catch(error => ({ error: error.message }));
      throw new Error(`expected label ${JSON.stringify(value)}: ${JSON.stringify({ document, supervisor, errors })}`, { cause });
    }
  };
  const status = async () => (await page.request.get(base + "/__pysx_dev__/status")).json();
  const metrics = async () => (await page.request.get(base + "/__fixture__/metrics")).json();
  const settledScroll = async (target, expected) => {
    await target.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert(Math.abs(await target.evaluate(() => scrollY) - expected) < 2, `expected scroll ${expected}`);
  };
  const volatile = Boolean((await metrics()).volatile);
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
  await page.evaluate(() => {
    window.retainedField = document.getElementById("field");
    window.retainedField.setSelectionRange(2, 4);
    window.retainedEntry = history.state.pysxEntry;
    window.scrollTo({top: 900, behavior: "instant"});
  });
  await other.evaluate(() => window.scrollTo({top: 1100, behavior: "instant"}));
  const before = await metrics();
  await edit("updated");
  await label(page, "updated"); await label(other, "updated");
  await connected(page); await connected(other);
  await count(page, 1); await count(other, 3);
  await settledScroll(page, 900); await settledScroll(other, 1100);
  assert(await page.evaluate(() => history.state.pysxEntry === window.retainedEntry));
  assert.equal(await page.locator("#field").inputValue(), "first draft");
  assert.equal(await other.locator("#field").inputValue(), "second draft");
  assert.equal(await page.evaluate(() => window.retainedField === document.getElementById("field")), true);
  assert.equal(await page.locator("#field").evaluate(el => el === document.activeElement), true);
  assert.deepEqual(await page.locator("#field").evaluate(el => [el.selectionStart, el.selectionEnd]), [2, 4]);
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

  await page.evaluate(() => window.scrollTo({top: 900, behavior: "instant"}));
  await edit("css");
  await page.waitForFunction(() => getComputedStyle(document.querySelector("main")).color === "rgb(0, 0, 255)");
  await count(page, 7); await count(other, 3);
  await connected(page); await settledScroll(page, 900);
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

  await page.evaluate(() => { location.hash = "note"; });
  await page.waitForFunction(() => document.activeElement?.id === "note");
  await page.evaluate(() => {
    window.retainedNote = document.getElementById("note");
    window.scrollTo({top: 900, behavior: "instant"});
  });
  await edit("hash"); await label(page, "hash"); await connected(page);
  await settledScroll(page, 900);
  const focusAfterHot = await page.evaluate(() => ({active: document.activeElement?.id,
    retained: window.retainedNote === document.getElementById("note")}));
  assert.equal(focusAfterHot.active, "note", JSON.stringify(focusAfterHot));
  await edit("shrink");
  await page.waitForFunction(() => getComputedStyle(document.querySelector("main")).minHeight === "300px");
  await connected(page); await settledScroll(page, 0);
  await edit("css");
  await page.waitForFunction(() => getComputedStyle(document.querySelector("main")).minHeight === "3000px");
  await connected(page);
  await page.evaluate(() => {
    window.scrollTo({top: 700, behavior: "instant"});
    history.pushState(null, "", "/?page=2");
    dispatchEvent(new PopStateEvent("popstate"));
  });
  await page.waitForFunction(() => scrollY === 0 && document.activeElement?.tagName === "MAIN");
  assert.equal(await page.locator("#note").getAttribute("tabindex"), null);
  await page.goBack();
  await page.waitForFunction(() => location.hash === "#note" && Math.abs(scrollY - 700) < 2);
  console.log("  ok  routed hot updates retain viewport/history/focus, clamp shorter pages and preserve navigation policy");

  await edit("incompatible");
  await count(page, 0); await count(other, 0); await connected(page);
  // An ordinary initializer change resets its value within the retained hot document.
  if (volatile) await settledScroll(page, 700);
  else await page.waitForFunction(() => document.activeElement?.id === "note" &&
    Math.abs(document.getElementById("note").getBoundingClientRect().top) < 2);
  assert.equal(await page.locator("#field").inputValue(), "initial");
  await edit("original"); await label(page, "original"); await label(other, "original");
  await connected(page); await connected(other);
  assert.deepEqual(errors, []);
  console.log("  ok  incompatible state follows the declared reset fallback");
  if (volatile) {
    await page.locator("#increment").click(); await count(page, 1);
    await page.reload(); await connected(page); await count(page, 0);
    await count(other, 0);
    const fresh = await context.newPage();
    await fresh.goto(base); await connected(fresh); await count(fresh, 0);
    await fresh.close();
    console.log("  ok  ordinary components need no persistence configuration and refresh/new tabs use defaults");
  }
  console.log(volatile ? "VOLATILE PRESERVING BROWSER PASSED" : "STATE PRESERVING BROWSER PASSED");
} finally { await browser.close(); }
