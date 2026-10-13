import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${process.argv[2]}/`);
  await page.waitForSelector("#todo-list li");
  const field = page.locator('input[type="text"]');
  await field.fill("Retained draft");
  await page.evaluate(() => { window.row = document.querySelector('#todo-list li[data-pysx-key="1"]'); });
  await page.getByRole("button", {name: "Active", exact: true}).click();
  await page.waitForFunction(() => document.querySelectorAll("#todo-list li").length === 2);
  assert.equal(await page.evaluate(() => window.row === document.querySelector('#todo-list li[data-pysx-key="1"]')), true);
  const oldHandler = await page.locator('#todo-list li[data-pysx-key="1"] input').getAttribute("data-pysx-change");
  await page.getByRole("button", {name: "Completed", exact: true}).click();
  await page.getByRole("button", {name: "Clear completed", exact: true}).click();
  await page.waitForSelector("#todo-empty");
  assert.match(await page.locator("#todo-empty").textContent(), /No completed todos/);
  assert.equal(await page.locator("#todo-list li").count(), 0);
  await field.focus();
  await page.evaluate(() => [...document.querySelectorAll("button")].find(button => button.textContent === "All").click());
  await page.waitForFunction(() => document.querySelectorAll("#todo-list li").length === 2);
  assert.equal(await page.locator("#todo-empty").count(), 0);
  assert.equal(await field.inputValue(), "Retained draft");
  assert.equal(await field.evaluate(node => node === document.activeElement), true);
  assert.deepEqual(await page.locator("#todo-list li").evaluateAll(rows => rows.map(row => row.dataset.pysxKey)), ["1", "2"]);
  const returned = page.locator('#todo-list li[data-pysx-key="1"] input');
  assert.notEqual(await returned.getAttribute("data-pysx-change"), oldHandler);
  await returned.check();
  await page.waitForFunction(() => document.getElementById("todo-summary").textContent.includes("1 item left"));
  console.log("  ok  empty completed filter explains recovery and restores keys with fresh working handlers");
  console.log("  ok  nonempty filters retain rows and empty-filter restoration preserves draft identity/focus");
  for (let count = await page.getByRole("button", {name: "Remove", exact: true}).count(); count > 0; count--) {
    await page.getByRole("button", {name: "Remove", exact: true}).first().click();
    await page.waitForFunction(count => document.querySelectorAll("#todo-list li").length === count, count - 1);
  }
  await page.waitForSelector("#todo-empty");
  assert.match(await page.locator("#todo-empty").textContent(), /No todos yet/);
  assert.deepEqual(errors, []);
  console.log("  ok  empty all filter gives a useful add-todo action without client errors");
  console.log("EMPTY FILTER BROWSER PASSED");
} finally { await browser.close(); }
