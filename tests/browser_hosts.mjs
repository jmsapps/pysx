import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const context = await browser.newContext({ extraHTTPHeaders: { "x-user": "alice" } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const base = `http://127.0.0.1:${process.argv[2]}/demo`;
  let releaseScript;
  const heldScript = new Promise(resolve => { releaseScript = resolve; });
  await page.route("**/client.js", async route => {
    await heldScript;
    await route.continue();
  });
  await page.goto(base + "/", { waitUntil: "commit" });
  await page.locator("#field").fill("before attachment");
  await page.evaluate(() => { window.originalMain = document.querySelector("main"); });
  assert.ok(await page.locator("#pysx-root").getAttribute("data-pysx-adoption"));
  releaseScript();
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "true");
  assert.equal(await page.evaluate(() => window.originalMain === document.querySelector("main")), true);
  await page.waitForFunction(() => document.getElementById("echo").textContent === "before attachment");
  assert.equal(await page.title(), "Shared hosts");
  assert.equal(await page.locator("#principal").textContent(), "alice");
  console.log("  ok  prefix, principal and dirty input survive same graph adoption");
  await page.waitForFunction(() => document.getElementById("resource").textContent === "loaded");
  await page.waitForFunction(() => document.getElementById("ticks").textContent === "1");
  await page.locator("#next").click();
  await page.waitForFunction(() => document.getElementById("route").textContent === "Next");
  assert.equal(new URL(page.url()).pathname, "/demo/next");
  await page.reload();
  assert.equal(await page.locator("#route").textContent(), "Next");
  console.log("  ok  independent async push and prefixed route deep reload");
  const asset = await context.request.get(base + "/sample.bin");
  assert.deepEqual(await asset.body(), Buffer.from([0, 255, ...Buffer.from("asset")]));
  assert.equal(asset.headers()["content-type"], "application/octet-stream");
  const cached = await context.request.get(base + "/sample.bin", { headers: { "if-none-match": asset.headers().etag } });
  assert.equal(cached.status(), 304);
  const head = await context.request.head(base + "/sample.bin");
  assert.equal(head.status(), 200);
  assert.equal(head.headers()["content-length"], "7");
  assert.deepEqual(errors, []);
  console.log("  ok  binary assets, MIME, cache validator and HEAD");
  console.log("HOST CONFIG BROWSER PASSED");
} finally { await browser.close(); }
