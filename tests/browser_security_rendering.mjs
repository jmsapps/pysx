import assert from "node:assert/strict";
import playwright from "playwright";
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const base = `http://127.0.0.1:${process.argv[2]}`;
try {
  const page = await browser.newPage();
  const violations = [];
  await page.addInitScript(() => {
    window.violations = [];
    document.addEventListener("securitypolicyviolation", event => window.violations.push(event.violatedDirective));
  });
  page.on("pageerror", error => violations.push(error.message));
  const response = await page.goto(base);
  const policy = response.headers()["content-security-policy"];
  assert.ok(policy.includes("script-src 'self'"));
  assert.ok(!policy.includes("unsafe-eval"));
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "true");
  assert.equal(await page.locator("#private img").count(), 0);
  assert.equal(await page.evaluate(() => Boolean(window.injected)), false);
  assert.equal(await page.locator("#inert-frame").evaluate(frame => Boolean(frame.contentWindow?.injected)), false);
  assert.match(await page.locator("#private").textContent(), /<img src=x/);
  assert.equal(await page.locator("#private").evaluate(el => getComputedStyle(el).color), "rgb(200, 0, 0)");
  assert.equal(await page.evaluate(() => getComputedStyle(document.body).backgroundColor), "rgb(240, 240, 240)");
  console.log("  ok  CSP allows SSR and adoption while hostile bootstrap/text stays inert");
  const sibling = await browser.newPage();
  await sibling.goto(base);
  await sibling.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "true");
  await page.locator("#update").click();
  await page.waitForFunction(() => document.getElementById("private").textContent.endsWith(" updated"));
  assert.equal(await page.locator("#private").evaluate(el => getComputedStyle(el).color), "rgb(0, 0, 200)");
  assert.equal(await page.locator("#private").evaluate(el => getComputedStyle(el).getPropertyValue("--gap")), "11px");
  assert.equal(await sibling.locator("#private").evaluate(el => getComputedStyle(el).color), "rgb(200, 0, 0)");
  const blocked = await page.evaluate(() => window.violations);
  assert.ok(blocked.length > 0);
  assert.ok(blocked.every(directive => directive.startsWith("script-src") || directive.startsWith("frame-src")));
  assert.deepEqual(violations, []);
  console.log("  ok  live stylesheet and variable updates work under CSP without sibling leakage");
  console.log("RENDERING BOOTSTRAP BROWSER PASSED");
} finally { await browser.close(); }
