import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${process.argv[2]}`);
  const values = () => page.locator("[id] > pysx-slot").evaluateAll(nodes =>
    Object.fromEntries(nodes.map(node => [node.parentElement.id, node.textContent])));
  const initial = {
    "t1-count-value": "2", doubled: "4", nested: "10", positional: "alpha", head: "alpha",
    "t2-count-value": "2", both: "False", either: "False", negated: "True", tagged: "True",
    "in-range": "True", "tag-count": "2", greeting: "hello ada", "named-eq": "True", differs: "True",
    "t3-count-value": "2", over: "True", under: "True", "at-least": "True", "at-most": "True",
    "over-named": "True", "under-named": "True", "at-least-named": "True", "at-most-named": "True",
    "t4-count-value": "2", "short-circuit": "True", "lambda-not": "True",
  };
  await page.waitForSelector("#doubled");
  assert.deepEqual(await values(), initial);
  for (const [control, expected] of [
    ["t1-count", { doubled: "6", both: "True", negated: "False", "at-most": "False", "at-most-named": "False", "lambda-not": "False" }],
    ["t1-nested", { nested: "11" }],
    ["t1-rotate", { positional: "beta", head: "beta" }],
    ["t2-name", { greeting: "hello grace", "named-eq": "False", differs: "False", "short-circuit": "False", both: "False", either: "True" }],
    ["t2-tag", { tagged: "False", "tag-count": "1" }],
  ]) {
    await page.locator(`#${control}`).click();
    await page.waitForFunction(expected => Object.entries(expected).every(([id, value]) => document.getElementById(id).textContent === value), expected);
  }
  console.log("  ok  arithmetic, projections, comparisons, boolean, collection and concat operations update");
  for (let i = 0; i < 7; i++) await page.locator("#t3-count").click();
  await page.waitForFunction(() => document.querySelector("#t3-count-value").textContent === "10");
  for (const id of ["under", "under-named", "in-range"]) assert.equal(await page.locator(`#${id}`).textContent(), "False");
  await page.locator("#t4-reset").click();
  await page.waitForFunction(() => document.querySelector("#doubled").textContent === "4");
  assert.deepEqual(await values(), initial);
  console.log("  ok  boundary comparisons and inclusive membership change; reset restores every public operation");
  console.log("OPERATOR CATALOG BROWSER PASSED");
} finally { await browser.close(); }
