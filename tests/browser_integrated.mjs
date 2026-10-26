import assert from "node:assert/strict";
import playwright from "playwright";

const controls = `http://127.0.0.1:${process.argv[2]}`;
const control = async action => {
  const response = await fetch(controls + action);
  assert.equal(response.status, 200);
  return response.json();
};
const {base} = await control("/reset");
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const context = await browser.newContext();
  await context.request.get(base + "/demo-login/writer", {maxRedirects: 0});
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(base + "/learn/capstone-resilience");
  const response = await context.request.get(base + "/assets/course.css");
  assert.equal(response.status(), 200);
  assert((await response.text()).includes("--accent"));
  const frame = await (await page.locator("#durable-frame").elementHandle()).contentFrame();
  const connected = () => frame.waitForFunction(() => document.getElementById("pysx-root")?.dataset.pysxConnection === "connected");
  const text = (id, value) => frame.waitForFunction(([id, value]) => document.getElementById(id)?.textContent === value, [id, value]);
  await connected();
  await frame.locator("#new-task").fill("Wheel application task");
  await frame.locator("#task-add").click();
  await frame.waitForSelector('[data-task="3"]');
  await frame.locator("#task-note-1").fill("Retained installed row");
  await frame.locator("#lab-draft").fill("Installed declared draft");
  await text("lab-echo", "Installed declared draft");
  await frame.locator("#lab-commit").click();
  await text("lab-count", "1");
  await text("lab-commit-status", "Allowed by current host policy");
  console.log("  ok  installed package assets, authenticated shared capstone and declared edits");
  for (const action of ["/restart", "/upgrade"]) {
    const setup = await frame.locator("#lab-setup").textContent();
    await control(action);
    await frame.waitForFunction(setup => document.querySelector("#lab-setup")?.textContent !== setup &&
      document.getElementById("pysx-root")?.dataset.pysxConnection === "connected", setup, {timeout: 30000});
    await text("lab-count", "1");
    assert.equal(await frame.locator("#lab-draft").inputValue(), "Installed declared draft");
    assert.equal(await frame.locator("#task-note-1").inputValue(), "Retained installed row");
    assert.equal(await frame.locator('[data-task="3"] h3').textContent(), "Wheel application task");
    await frame.locator("#lab-resource-fail").click(); await text("lab-resource-status", "error");
    await frame.locator("#lab-resource-retry").click(); await text("lab-resource-status", "success");
    console.log(`  ok  ${action}: actual process loss, installed state reconstruction and fresh async retry`);
  }
  const isolated = await context.newPage();
  await isolated.goto(base + "/labs/durable");
  await isolated.waitForFunction(() => document.getElementById("pysx-root")?.dataset.pysxConnection === "connected");
  assert.equal(await isolated.locator("#lab-draft").inputValue(), "");
  assert.equal(await isolated.locator("#lab-count").textContent(), "0");
  assert.deepEqual(errors, []);
  console.log("  ok  new installed application document remains isolated");
  console.log("INTEGRATED TUTORIAL BROWSER PASSED");
} finally { await browser.close(); }
