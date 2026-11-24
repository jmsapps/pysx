import assert from "node:assert/strict";
import playwright from "playwright";

const engine = playwright[process.env.PYSX_BROWSER_ENGINE];
const url = `http://127.0.0.1:${Number(process.argv[2])}/`;
const settled = page => page.waitForFunction(
  () => document.getElementById("value").textContent !== "unset", null, { timeout: 45000 });
const valueIs = (page, text) => page.waitForFunction(
  expected => document.getElementById("value").textContent === expected, text, { timeout: 45000 });
const failureIs = (page, text) => page.waitForFunction(
  expected => document.getElementById("failure").textContent === expected, text, { timeout: 45000 });

const browser = await engine.launch();
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(url);

  const sources = await page.locator("script").evaluateAll(nodes => nodes.map(el => el.src || el.textContent));
  assert.equal(sources.filter(source => !/\/client\.js$/.test(source) && !source.includes("/__pysx__/")).length, 0,
    `the page must carry no application JavaScript: ${JSON.stringify(sources)}`);
  console.log("  ok  the application ships no inline or own-file JavaScript");

  await settled(page);
  await valueIs(page, "missing");
  console.log("  ok  an absent key restores as missing, not as an error");

  await page.locator("#write").click();
  await valueIs(page, "dark");
  const stored = await page.evaluate(() => window.localStorage.getItem("pysx:app:theme"));
  assert.equal(stored, "dark", "the write must reach localStorage under the scoped key");
  console.log("  ok  a Python write reaches localStorage under its application-scoped key");

  await page.reload();
  await settled(page);
  await valueIs(page, "dark");
  console.log("  ok  the stored value survives a reload and restores at mount");

  await page.locator("#write-layout").click();
  await valueIs(page, "layout-written");
  await page.locator("#read-layout").click();
  await valueIs(page, "{'sidebar': 'collapsed', 'width': 240}");
  console.log("  ok  structured values round trip through set_json and get_json");

  await page.locator("#read-corrupt").click();
  await valueIs(page, "raw:{not json");
  console.log("  ok  an undecodable value raises and stays reachable through the raw read");

  await page.locator("#oversized").click();
  await failureIs(page, "rejected:StorageError");
  console.log("  ok  an oversized value is rejected before it reaches the browser");

  await page.locator("#drop").click();
  await valueIs(page, "removed");
  await page.locator("#read-back").click();
  await valueIs(page, "missing");
  assert.equal(await page.evaluate(() => window.localStorage.getItem("pysx:app:theme")), null);
  console.log("  ok  removal clears the key and the next read reports it missing");

  const second = await context.newPage();
  await second.goto(url);
  await settled(second);
  await second.locator("#write").click();
  await valueIs(second, "dark");
  await page.waitForFunction(
    () => document.getElementById("watched").textContent === "dark", null, { timeout: 45000 });
  console.log("  ok  a write in another tab reaches a watcher in the first");

  await second.locator("#drop").click();
  await page.waitForFunction(
    () => document.getElementById("watched").textContent === "removed", null, { timeout: 45000 });
  console.log("  ok  a removal in another tab reports as a missing value");
  await second.close();

  assert.equal(await page.locator("#server-cookie").textContent(), "none",
    "the first render saw no application cookie");
  await page.locator("#cookie-write").click();
  await valueIs(page, "cookie-written");
  await page.locator("#cookie-read").click();
  await valueIs(page, "cookie:compact");
  console.log("  ok  a browser cookie write is readable back through Python");

  await page.reload();
  await settled(page);
  assert.equal(await page.locator("#server-cookie").textContent(), "compact",
    "a browser-written cookie must reach the next server render");
  console.log("  ok  a cookie written in the browser reaches the next server render");

  await page.locator("#cookie-identity").click();
  await failureIs(page, "reserved:StorageError");
  console.log("  ok  the framework identity cookie is refused by name");

  await page.locator("#cookie-drop").click();
  await valueIs(page, "cookie-removed");
  await page.reload();
  await settled(page);
  assert.equal(await page.locator("#server-cookie").textContent(), "none",
    "removal must clear the cookie for the next render too");
  console.log("  ok  scoped removal clears the cookie the next render sees");

  assert.deepEqual(errors, []);
  await page.close();
  await context.close();

  const denied = await browser.newContext();
  const blocked = await denied.newPage();
  const blockedErrors = [];
  blocked.on("pageerror", error => blockedErrors.push(error.message));
  await blocked.addInitScript(() => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() { throw new DOMException("storage is denied", "SecurityError"); },
    });
  });
  await blocked.goto(url);
  await blocked.locator("#probe").click();
  await failureIs(blocked, "unavailable");
  assert.deepEqual(blockedErrors, []);
  console.log("  ok  denied storage raises StorageUnavailableError rather than a missing key");
  await denied.close();

  console.log("BROWSER STORAGE PASSED");
} finally { await browser.close(); }
