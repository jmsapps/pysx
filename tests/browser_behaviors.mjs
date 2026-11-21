import assert from "node:assert/strict";
import playwright from "playwright";

const engine = playwright[process.env.PYSX_BROWSER_ENGINE];
const url = `http://127.0.0.1:${Number(process.argv[2])}/`;
const revealed = (page, id) => page.locator(`#card-${id}`).evaluate(el => el.classList.contains("is-visible"));
const settled = page => page.waitForFunction(
  () => document.getElementById("status").textContent === "Connected", null, { timeout: 45000 });

const browser = await engine.launch();
try {
  const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    const NativeObserver = window.IntersectionObserver;
    window.__revealTargets = new Set();
    window.IntersectionObserver = class extends NativeObserver {
      constructor(callback, options) { super(callback, options); this.owned = new Set(); }
      observe(target) { this.owned.add(target); window.__revealTargets.add(target); super.observe(target); }
      unobserve(target) { this.owned.delete(target); window.__revealTargets.delete(target); super.unobserve(target); }
      disconnect() {
        for (const target of this.owned) window.__revealTargets.delete(target);
        this.owned.clear();
        super.disconnect();
      }
    };
  });
  await page.goto(url);

  const sources = await page.locator("script").evaluateAll(nodes => nodes.map(el => el.src || el.textContent));
  assert.equal(sources.filter(source => !/\/client\.js$/.test(source) && !source.includes("/__pysx__/")).length, 0,
    `the page must carry no application JavaScript: ${JSON.stringify(sources)}`);
  console.log("  ok  the application ships no inline or own-file JavaScript");

  await settled(page);
  assert.equal(await page.locator("#drop").isDisabled(), false);
  console.log("  ok  connection text and a disabled control are declared in ordinary markup");

  await page.waitForFunction(() => document.getElementById("card-a").classList.contains("is-visible"));
  assert.equal(await revealed(page, "b"), false, "a card below the fold waits");
  await page.locator("#card-b").scrollIntoViewIfNeeded();
  await page.waitForFunction(() => document.getElementById("card-b").classList.contains("is-visible"));
  console.log("  ok  viewport entry reveals a card with no application observer");

  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(100);
  assert.equal(await revealed(page, "b"), true, "a once-only reveal is not undone");
  console.log("  ok  a once-only reveal survives leaving the viewport again");

  await page.locator("#add").click();
  await page.waitForFunction(() => [...window.__revealTargets].some(el => el.id === "card-late"));
  await page.locator("#remove").click();
  await page.waitForFunction(() => document.getElementById("card-late") === null);
  assert.equal(await page.evaluate(() => [...window.__revealTargets].some(el => el.id === "card-late")), false,
    "removing an unrevealed card releases its observer registration");

  await page.locator("#add").click();
  await page.waitForSelector("#card-late");
  assert.equal(await revealed(page, "late"), false);
  await page.locator("#card-late").scrollIntoViewIfNeeded();
  await page.waitForFunction(() => document.getElementById("card-late").classList.contains("is-visible"));
  await page.locator("#remove").click();
  await page.waitForFunction(() => document.getElementById("card-late") === null);
  console.log("  ok  a card inserted by a server patch is observed, and removal cleans up");

  await page.locator("#reorder").click();
  await page.waitForFunction(() => document.querySelector("#cards .card").id === "card-b");
  assert.equal(await revealed(page, "b"), true, "keyed reordering keeps each element's own state");
  console.log("  ok  keyed reordering preserves per-element reveal state");

  const beforeDrop = Date.now();
  await page.locator("#drop").click();
  await page.waitForFunction(() => document.getElementById("status").textContent !== "Connected");
  assert.equal(await page.locator("#drop").isDisabled(), true);
  assert.equal(await page.evaluate(() => [...window.__revealTargets].some(el => el.id === "card-pending")), true,
    "browser-local observations stay active while the transport is down");
  await settled(page);
  assert.ok(Date.now() - beforeDrop >= 900, "the disconnect lasts the declared duration");
  console.log("  ok  a local action drops the transport for its bounded duration and resumes");

  await page.locator("#card-pending").scrollIntoViewIfNeeded();
  await page.waitForFunction(() => document.getElementById("card-pending").classList.contains("is-visible"));
  await page.locator("#card-repeat").scrollIntoViewIfNeeded();
  await page.waitForFunction(() => document.getElementById("card-repeat").classList.contains("is-visible"));
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForFunction(() => !document.getElementById("card-repeat").classList.contains("is-visible"));
  await page.locator("#drop").click();
  await page.waitForFunction(() => document.getElementById("status").textContent !== "Connected");
  await settled(page);
  await page.locator("#card-repeat").scrollIntoViewIfNeeded();
  await page.waitForFunction(() => document.getElementById("card-repeat").classList.contains("is-visible"));
  console.log("  ok  pending and repeating reveals survive reconnect without application JavaScript");

  await page.locator("#panel").evaluate(el => el.scrollTo(0, 400));
  await page.locator("#go-next").click();
  await page.waitForFunction(() => document.getElementById("route").textContent === "next");
  await page.goBack();
  await page.waitForFunction(() => document.getElementById("route").textContent === "home");
  await page.waitForFunction(() => document.getElementById("panel").scrollTop > 300);
  console.log("  ok  history navigation restores the nominated panel, not the window");

  assert.deepEqual(errors, []);
  await page.close();

  const reduced = await browser.newPage({ viewport: { width: 800, height: 600 } });
  const reducedErrors = [];
  reduced.on("pageerror", error => reducedErrors.push(error.message));
  await reduced.emulateMedia({ reducedMotion: "reduce" });
  await reduced.goto(url);
  await settled(reduced);
  assert.equal(await revealed(reduced, "b"), true, "reduced motion reveals without observing");
  assert.deepEqual(reducedErrors, []);
  console.log("  ok  a reduced-motion visitor sees every card without waiting for the viewport");

  console.log("BROWSER BEHAVIORS PASSED");
} finally { await browser.close(); }
