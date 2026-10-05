import assert from "node:assert/strict";
import playwright from "playwright";

const engine = process.env.PYSX_BROWSER_ENGINE;
if (!["chromium", "firefox", "webkit"].includes(engine)) throw new Error("missing engine");
if (process.platform === "darwin") delete process.env.DYLD_LIBRARY_PATH;
const browser = await playwright[engine].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${Number(process.argv[2])}/`);
  await page.waitForSelector("#serialization");
  assert.equal(await page.locator("#text").inputValue(), 'a<&"');
  assert.equal(await page.locator("#area").inputValue(), 'a<&"');
  assert.equal(await page.locator("#select").inputValue(), "b");
  assert.equal(await page.locator("#hidden").getAttribute("aria-hidden"), "false");
  assert.equal(await page.locator("#checked").isChecked(), false);
  assert.equal(await page.locator("label").getAttribute("for"), "text");
  assert.equal(await page.locator("#text").evaluate(el => el.maxLength), 20);
  assert.equal(await page.locator("#text").evaluate(el => el.tabIndex), 2);
  assert.equal(await page.locator("#text").evaluate(el => el.readOnly), false);
  console.log("  ok  initial native booleans, aliases, text and selection properties");
  assert.equal(await page.locator("#vector").evaluate(el => el.namespaceURI), "http://www.w3.org/2000/svg");
  assert.equal(await page.locator("#vector circle").evaluate(el => el.namespaceURI), "http://www.w3.org/2000/svg");
  assert.equal(await page.locator("#foreign").evaluate(el => el.namespaceURI), "http://www.w3.org/1999/xhtml");
  assert.equal(await page.locator("#math mi").evaluate(el => el.namespaceURI), "http://www.w3.org/1998/Math/MathML");
  assert.equal(await page.locator("#vector").getAttribute("viewBox"), "0 0 10 10");
  console.log("  ok  foreign namespace inheritance and HTML reentry");
  await page.locator("#text").fill("user edited");
  await page.locator("#area").fill("user edited");
  await page.locator("#properties").click();
  await page.waitForFunction(() => document.getElementById("text").value === "server");
  assert.equal(await page.locator("#area").inputValue(), "server");
  assert.equal(await page.locator("#checked").isChecked(), true);
  assert.equal(await page.locator("#selected").inputValue(), "b");
  assert.equal(await page.locator("#hidden").getAttribute("aria-hidden"), "true");
  assert.equal(await page.locator("#hidden").isVisible(), false);
  assert.equal(await page.locator("#custom").getAttribute("data-value"), "server");
  assert.equal(await page.locator("#custom").getAttribute("class"), 'first second "quoted"');
  assert.deepEqual(errors, []);
  console.log("  ok  dirty controls accept authoritative properties and classes merge safely");
  await page.close();
  console.log("SERIALIZATION BROWSER PASSED");
} finally {
  await browser.close();
}
