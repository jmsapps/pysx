import assert from "node:assert/strict";
import playwright from "playwright";

const engine = process.env.PYSX_BROWSER_ENGINE;
if (!["chromium", "firefox", "webkit"].includes(engine)) throw new Error("missing browser engine");
if (process.platform === "darwin") delete process.env.DYLD_LIBRARY_PATH;
const browser = await playwright[engine].launch();
const booleanValues = async (page) => Promise.all(
  ["derived-all", "derived-any", "derived-not"].map(async (id) =>
    (await page.locator(`#${id}`).textContent()).split(": ").at(-1)),
);
try {
  const page = await browser.newPage();
  const other = await browser.newPage();
  const address = `http://127.0.0.1:${Number(process.argv[2])}/`;
  await Promise.all([page.goto(address), other.goto(address)]);
  await Promise.all([page.waitForSelector("#operator-status"), other.waitForSelector("#operator-status")]);
  assert.equal(await page.locator("#operator-status").textContent(), "First score: 10");
  assert.equal(await page.locator("#operator-size").textContent(), "Rows: 3");
  assert.equal(await page.locator("#operator-branch").count(), 0);
  assert.deepEqual(await booleanValues(page), ["False", "True", "True"]);
  console.log("  ok  unified projections, concat and collection length render initial values");
  await page.getByRole("button", { name: "Advance twice", exact: true }).click();
  await page.waitForSelector("#operator-branch");
  assert((await page.locator("body").textContent()).includes("doubled + tripled: 15"));
  assert.equal(await other.locator("#operator-branch").count(), 0);
  assert.deepEqual(await booleanValues(page), ["True", "True", "False"]);
  console.log("  ok  mixed numeric ordering and boolean membership update one session");
  await page.getByRole("button", { name: "Increment first", exact: true }).click();
  await page.waitForFunction(() => document.getElementById("operator-status").textContent === "First score: 11");
  await page.waitForFunction(() => !document.getElementById("operator-branch"));
  assert.equal(await other.locator("#operator-status").textContent(), "First score: 10");
  assert.deepEqual(await booleanValues(page), ["False", "True", "True"]);
  console.log("  ok  nested write-through updates reactive text and removes the branch independently");
  await page.getByRole("button", { name: "Edit a private snapshot", exact: true }).click();
  await page.getByRole("button", { name: "Rotate list", exact: true }).click();
  await page.waitForFunction(() => document.body.textContent.includes("Order: 20, 30, 10 | first position: 20"));
  assert.equal(await page.locator("#operator-status").textContent(), "First score: 11");
  console.log("  ok  snapshot mutation stays private and positional projections follow reorder");
  await page.getByRole("button", { name: "Reset count", exact: true }).click();
  await page.waitForFunction(() => document.getElementById("derived-any").textContent.endsWith("False"));
  assert.deepEqual(await booleanValues(page), ["False", "False", "True"]);
  assert.deepEqual(await booleanValues(other), ["False", "True", "True"]);
  console.log("  ok  all_of, any_of and not_ visibly react to their inputs");
  await Promise.all([page.close(), other.close()]);
  console.log("OPERATORS BROWSER PASSED");
} finally {
  await browser.close();
}
