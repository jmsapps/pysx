import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${process.argv[2]}`);
  const route = path => page.waitForFunction(path =>
    document.querySelector("#route-path")?.textContent === path && location.pathname === path, path);
  const click = id => page.evaluate(id => document.getElementById(id).click(), id);
  const focused = id => page.waitForFunction(id => document.activeElement?.id === id, id);
  const atTop = async () => {
    try { await page.waitForFunction(() => scrollY < 5, null, {timeout: 3000}); }
    catch (error) {
      console.log(await page.evaluate(() => ({scroll: scrollY, url: location.href,
        focus: document.activeElement?.id})));
      throw error;
    }
  };
  const atTarget = () => page.waitForFunction(() => {
    const target = document.getElementById("café");
    return target && document.activeElement === target && target.getBoundingClientRect().top < 5;
  });
  await route("/");
  await click("delayed"); await route("/delayed"); await atTarget();
  assert.equal(new URL(page.url()).hash, "#caf%C3%A9");
  assert.equal(await page.locator('[id="café"]').getAttribute("tabindex"), "-1");
  console.log("  ok  decoded late mount target scrolls and receives accessible focus after its patch");

  await page.evaluate(() => window.scrollTo(0, 100));
  await click("fragment"); await atTarget();
  await click("named-link"); await focused("");
  assert.equal(await page.evaluate(() => document.activeElement.getAttribute("name")), "named");
  await click("first"); await route("/first"); await atTop();
  await page.evaluate(() => {
    document.querySelector("#restore-focus").focus({preventScroll: true});
    window.scrollTo(0, 600);
  });
  await page.waitForFunction(() => Math.abs(scrollY - 600) < 5);
  await click("second"); await route("/second"); await atTop();
  await focused("navigation-root");
  await page.evaluate(() => {
    document.querySelector("#restore-focus").focus({preventScroll: true});
    window.scrollTo(0, 450);
  });
  await page.goBack(); await route("/first");
  await page.waitForFunction(() => Math.abs(scrollY - 600) < 5);
  await focused("restore-focus");
  await page.goForward(); await route("/second");
  await page.waitForFunction(() => Math.abs(scrollY - 450) < 5);
  await focused("restore-focus");
  console.log("  ok  repeated/native/named anchors and back/forward restore scroll and focus");

  await click("missing"); await route("/last"); await atTop();
  await focused("navigation-root");
  await page.evaluate(() => window.scrollTo(0, 300));
  await click("same-missing");
  await page.waitForFunction(() => location.hash === "#absent");
  await page.waitForTimeout(150);
  assert.ok(Math.abs(await page.evaluate(() => scrollY) - 300) < 5);
  console.log("  ok  missing targets use a cross-route fallback and preserve same-route position");

  await page.evaluate(() => {
    document.querySelector("#slow").click();
    document.querySelector("#last").click();
  });
  await route("/last"); await atTop(); await focused("navigation-root");
  await page.waitForTimeout(200);
  assert.equal(new URL(page.url()).hash, "");
  assert.ok(await page.evaluate(() => scrollY) < 5);
  assert.deepEqual(errors, []);
  console.log("  ok  rapid navigations cancel delayed fragment scrolling from a stale route");
  console.log("FRAGMENT BROWSER PASSED");
} finally { await browser.close(); }
