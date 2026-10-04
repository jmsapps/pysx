import assert from "node:assert/strict";
import playwright from "playwright";

const engine = process.env.PYSX_BROWSER_ENGINE;
if (!["chromium", "firefox", "webkit"].includes(engine)) throw new Error("missing proof engine");
if (process.platform === "darwin") delete process.env.DYLD_LIBRARY_PATH;
const browser = await playwright[engine].launch();
try {
  const page = await browser.newPage();
  let sockets = 0;
  page.on("websocket", () => sockets++);
  const response = await page.goto(`http://127.0.0.1:${Number(process.argv[2])}/`);
  assert.equal(response.status(), 200);
  const html = await response.text();
  assert(html.includes('id="proof-count"') && html.includes('value="server"'));
  assert.equal(await page.locator("#proof-count").textContent(), "0");
  const setupSerial = await page.locator("#proof-root").getAttribute("data-setup-serial");
  assert(setupSerial);
  assert.equal(sockets, 0);
  assert.equal(await page.locator("#proof-count").evaluate((node) => getComputedStyle(node).color), "rgb(20, 70, 120)");
  console.log("  ok  meaningful HTTP HTML and scoped CSS exist before any socket");

  await page.locator("#proof-input").fill("edited before attachment 🐍");
  await page.locator("#proof-input").focus();
  await page.evaluate(() => {
    window.proofOriginalRoot = document.getElementById("proof-root");
    window.proofOriginalInput = document.getElementById("proof-input");
  });
  await page.evaluate(() => window.proofAttach());
  assert.equal(sockets, 1);
  assert.equal(await page.locator("#proof-root").getAttribute("data-adopted"), "yes");
  assert.equal(await page.locator("#proof-root").getAttribute("data-setup-serial"), setupSerial);
  assert(await page.evaluate(() => document.getElementById("proof-root") === window.proofOriginalRoot));
  console.log("  ok  socket adopts the HTTP graph with exactly one setup and no root replacement");
  assert.equal(await page.locator("#proof-input").inputValue(), "edited before attachment 🐍");
  assert(await page.evaluate(() => document.activeElement === window.proofOriginalInput));
  assert(await page.evaluate(() => document.getElementById("proof-input") === window.proofOriginalInput));
  console.log("  ok  pre-attachment user value, focus and input identity survive adoption");

  await page.locator("#proof-increment").click();
  await page.waitForFunction(() => document.getElementById("proof-count").textContent === "1");
  assert.equal(await page.locator("#proof-input").inputValue(), "edited before attachment 🐍");
  assert(await page.evaluate(() => document.getElementById("proof-root") === window.proofOriginalRoot));
  assert(await page.evaluate(() => document.getElementById("proof-input") === window.proofOriginalInput));
  console.log("  ok  adopted live state handles events and applies a selective text patch");
  console.log("ARCHITECTURE ADOPTION PASSED");
} finally {
  await browser.close();
}
