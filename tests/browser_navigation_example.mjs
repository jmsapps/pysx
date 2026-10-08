import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${process.argv[2]}`);
  const text = (id, value) => page.waitForFunction(([id, value]) =>
    document.getElementById(id)?.textContent === value &&
    (id !== "current-url" || location.pathname + location.search + location.hash === value), [id, value]);
  const click = id => page.evaluate(id => document.getElementById(id).click(), id);
  await text("route-title", "Home");
  await page.locator("#user-one").click(); await text("route-title", "User 1");
  await page.locator("#increment").click();
  await page.waitForFunction(() => document.querySelector("#increment").textContent.trim() === "Count: 1");
  await page.locator("#user-two").click(); await text("route-title", "User 2");
  assert.ok((await page.locator("#increment").textContent()).includes("1"));
  await text("current-url", "/users/2?tab=activity");
  await click("sibling"); await text("route-title", "User 3");
  await text("current-url", "/users/3");
  await click("descend"); await text("route-title", "Activity for user 3");
  await click("parent"); await text("route-title", "User 3");
  console.log("  ok  styled links preserve parameter state and show relative nested routes");

  await click("query"); await text("current-url", "/users/3/?tab=settings");
  await page.locator("#history-focus").evaluate(node => node.focus({preventScroll: true}));
  await page.evaluate(() => window.scrollTo(0, 350));
  await click("files"); await text("route-title", "Files");
  await page.goBack(); await text("current-url", "/users/3/?tab=settings");
  try {
    await page.waitForFunction(() => Math.abs(scrollY - 350) < 5 && document.activeElement.id === "history-focus",
      null, {timeout: 5000});
  } catch (error) {
    console.log(await page.evaluate(() => ({scroll: scrollY, focus: document.activeElement?.id,
      url: location.href})));
    throw error;
  }
  await click("fragment");
  await page.waitForFunction(() => location.hash === "#details" && document.activeElement.id === "details");
  assert.ok(await page.locator("#details").evaluate(node => {
    const rect = node.getBoundingClientRect();
    return rect.top >= -5 && rect.bottom <= innerHeight && scrollY > 350;
  }));
  console.log("  ok  query replacement, browser history, focus restoration and fragment scrolling");

  const url = page.url();
  await click("cancel"); await text("notice", "Navigation cancelled. Your current route stays open.");
  assert.equal(page.url(), url);
  const other = await browser.newPage();
  await other.goto(`http://127.0.0.1:${process.argv[2]}`);
  await other.waitForFunction(() => document.querySelector("#route-title")?.textContent === "Home");
  assert.equal(new URL(other.url()).pathname, "/");
  console.log("  ok  explicit cancellation and a second browser session keep independent state");

  const historyLength = await page.evaluate(() => history.length);
  await click("require-login"); await text("current-url", "/login");
  await text("route-title", "Sign in"); await text("redirect-status", "Sign-in required");
  assert.equal(await page.evaluate(() => history.length), historyLength);
  assert.equal(await other.locator("#redirect-status").textContent(), "Browsing freely");
  assert.equal(new URL(other.url()).pathname, "/");
  await click("home");
  await page.waitForFunction(length => history.length === length + 1 && location.pathname === "/login",
    historyLength);
  await text("current-url", "/login"); await text("route-title", "Sign in");
  await click("login-continue"); await text("current-url", "/");
  await text("route-title", "Home"); await text("redirect-status", "Browsing freely");
  assert.equal(await page.evaluate(() => history.length), historyLength + 2);
  await click("user-one"); await text("current-url", "/users/1");
  await text("route-title", "User 1");
  assert.deepEqual(errors, []);
  console.log("  ok  signal-driven effect redirects replace history and Continue restores browsing");
  console.log("NAVIGATION EXAMPLE BROWSER PASSED");
} finally { await browser.close(); }
