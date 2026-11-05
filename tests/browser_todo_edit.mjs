import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const other = await browser.newPage();
  const address = `http://127.0.0.1:${process.argv[2]}`;
  await Promise.all([page.goto(address), other.goto(address)]);
  const row = page.locator('li[data-pysx-key="1"]');
  const original = await row.elementHandle();
  await row.locator("[data-edit]").click();
  await page.locator("#todo-edit-1").fill("Updated title");
  await page.waitForFunction(() => document.querySelector("#todo-edit-1").value === "Updated title");
  assert(await page.locator("#todo-edit-1").evaluate(node => node === document.activeElement));
  await row.locator("[data-save]").click();
  await page.waitForFunction(() => document.querySelector('li[data-pysx-key="1"] .todo-text').textContent === "Updated title");
  assert(await original.evaluate(node => node === document.querySelector('li[data-pysx-key="1"]')));
  assert.equal(await other.locator('li[data-pysx-key="1"] .todo-text').textContent(), "Wire up signals");
  await row.locator('input[type="checkbox"]').check();
  await page.waitForFunction(() => document.querySelector('li[data-pysx-key="1"]').className.includes("is-done"));
  assert.equal(await row.locator(".todo-text").textContent(), "Updated title");
  await row.getByRole("button", {name: "Remove", exact: true}).click();
  await page.waitForFunction(() => !document.querySelector('li[data-pysx-key="1"]'));
  console.log("  ok  edited keyed todos retain focus/row identity and current toggle/remove handlers in isolated sessions");
  console.log("TODO EDIT BROWSER PASSED");
} finally { await browser.close(); }
