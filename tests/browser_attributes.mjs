import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const other = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const url = `http://127.0.0.1:${process.argv[2]}`;
  await Promise.all([page.goto(url), other.goto(url)]);
  await page.waitForSelector("#attributes-example");
  const presence = () => page.evaluate(() => ({
    readonly: document.querySelector("#attribute-field").readOnly,
    required: document.querySelector("#required-field").required,
    open: document.querySelector("#attribute-details").open,
    checked: document.querySelector("#attribute-check").checked,
    disabled: document.querySelector("#attribute-check").disabled,
    hidden: document.querySelector("#attribute-panel").hidden,
    controls: document.querySelector("#attribute-video").controls,
    loop: document.querySelector("#attribute-video").loop,
    muted: document.querySelector("#attribute-video").muted,
    inline: document.querySelector("#attribute-video").hasAttribute("playsinline"),
  }));
  assert(Object.values(await presence()).every(value => value === false), JSON.stringify(await presence()));
  assert.equal(await page.locator("#attribute-form").getAttribute("novalidate"), null);
  assert.equal(await page.locator("#attribute-video").getAttribute("autoplay"), null);
  assert.equal(await page.locator("#toggle-attrs").getAttribute("aria-expanded"), "false");
  assert.equal(await page.locator("label[for=attribute-field]").count(), 1);
  assert.equal(await page.locator("#attribute-field").evaluate(node => node.maxLength), 40);
  assert.equal(await page.locator("#attribute-field").getAttribute("autofocus"), null);
  assert.equal(await page.locator("#attribute-autofocus").evaluate(node => node.autofocus), true);
  await page.waitForFunction(() => document.activeElement?.id === "attribute-autofocus");
  assert.equal(await page.locator("#attribute-custom").getAttribute("data-reveal-delay"), "200");
  for (const [name, value] of Object.entries({"x-state": "ready", "x-tone": "calm", "hx-get": "/api", "hx-swap": "outerHTML"})) {
    assert.equal(await page.locator("#attribute-raw").getAttribute(name), value);
  }
  await page.locator("#attribute-field").fill("editable");
  await page.locator("#attribute-submit").click();
  await page.waitForFunction(() => document.querySelector("#attribute-submits").textContent === "1");
  await page.locator("#toggle-attrs").click();
  await page.waitForFunction(() => document.querySelector("#attribute-field").readOnly);
  assert(Object.values(await presence()).every(value => value === true));
  assert.equal(await page.locator("#toggle-attrs").getAttribute("aria-expanded"), "true");
  assert.equal(await page.locator("#attribute-panel").getAttribute("aria-hidden"), "true");
  await page.locator("#attribute-submit").click();
  assert.equal(await page.locator("#required-field").evaluate(node => node.validity.valueMissing), true);
  assert.equal(await page.locator("#attribute-submits").textContent(), "1");
  await page.locator("#required-field").fill("valid");
  await page.locator("#attribute-submit").click();
  await page.waitForFunction(() => document.querySelector("#attribute-submits").textContent === "2");
  await page.locator("#toggle-attrs").click();
  await page.waitForFunction(() => !document.querySelector("#attribute-field").readOnly);
  assert(Object.values(await presence()).every(value => value === false));
  assert.equal(await page.locator("#attribute-panel").getAttribute("aria-hidden"), "false");
  console.log("  ok  native boolean presence/removal, aliases, ARIA strings and browser validation");

  const row = await page.locator('[data-row="1"]').elementHandle();
  const chooser = await page.locator('[data-choose="1"]').elementHandle();
  await page.locator('[data-increment="1"]').click();
  await page.waitForFunction(() => document.querySelector('[data-row="1"]').dataset.n === "1");
  assert.equal(await page.locator('[data-row="1"] [data-inner]').getAttribute("data-inner"), "1");
  assert.equal(await page.locator('[data-row="2"]').getAttribute("data-n"), "0");
  await page.locator("#retone").click();
  await page.waitForFunction(() => document.querySelector("#attribute-custom").getAttribute("data-tone") === "bright");
  assert.equal(await page.locator("#attribute-raw").getAttribute("x-tone"), "bright");
  assert.deepEqual(await page.locator("[data-row]").evaluateAll(nodes => nodes.map(node => node.dataset.tone)), ["bright", "bright"]);
  assert.equal(await page.locator('[data-row="1"]').getAttribute("data-n"), "1");
  await page.locator("#reverse-attrs").click();
  await page.waitForFunction(() => document.querySelector("[data-row]").dataset.row === "2");
  assert(await row.evaluate(node => node === document.querySelector('[data-row="1"]')));
  assert(await chooser.evaluate(node => node === document.querySelector('[data-choose="1"]')));
  assert.equal(await row.evaluate(node => node.className), "changed");
  await page.locator('[data-choose="1"]').click();
  await page.waitForFunction(() => document.querySelector("#attribute-picked").textContent === "Row 1: 1");
  assert.equal(await other.locator('[data-row="1"]').getAttribute("data-n"), "0");
  assert.equal(await other.locator("#attribute-custom").getAttribute("data-tone"), "calm");
  assert.deepEqual(errors, []);
  console.log("  ok  independent keyed attrs, classes and current row handlers retain nodes and isolate sessions");
  console.log("ATTRIBUTES BROWSER PASSED");
} finally { await browser.close(); }
