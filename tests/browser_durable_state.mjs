import assert from "node:assert/strict";
import playwright from "playwright";

const controls = `http://127.0.0.1:${process.argv[2]}`;
const control = async path => {
  const response = await fetch(controls + path);
  assert.equal(response.status, 200);
  return response.json();
};
const {base} = await control("/reset");
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const context = await browser.newContext({ignoreHTTPSErrors: true});
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const connected = async () => page.waitForFunction(() =>
    document.getElementById("pysx-root").dataset.pysxConnection === "connected", null, {timeout: 10000});
  const response = await page.goto(base + "/durable/deep/link?owner=worker-a");
  assert.equal(response.status(), 200);
  assert.ok((await response.text()).includes("Declared session state"));
  await connected();
  await page.waitForFunction(() => document.getElementById("durable-resource").textContent === "Resource rebuilt");
  await page.locator("#durable-draft").fill("A draft preserved across worker loss");
  await page.waitForFunction(() => document.getElementById("durable-echo").textContent === "A draft preserved across worker loss");
  await page.locator("#durable-increment").click();
  await page.waitForFunction(() => document.getElementById("durable-count").textContent === "1");
  const generation = await page.locator("#durable-generation").textContent();
  console.log("  ok  runnable durable example renders shared components, saves a draft and mounts a resource");

  await control("/kill?worker=worker-a");
  await page.waitForFunction(generation => document.getElementById("pysx-root").dataset.pysxConnection === "connected" &&
    document.getElementById("durable-generation").textContent !== generation, generation, {timeout: 10000});
  assert.equal(await page.locator("#durable-count").textContent(), "1");
  assert.equal(await page.locator("#durable-draft").inputValue(), "A draft preserved across worker loss");
  await page.waitForFunction(() => document.getElementById("durable-resource").textContent === "Resource rebuilt");
  assert.match(await page.locator("#pysx-connection-status").textContent(), /restored/);
  await page.locator("#durable-increment").click();
  await page.waitForFunction(() => document.getElementById("durable-count").textContent === "2");
  console.log("  ok  worker loss preserves committed typed state and rebuilds working handlers and resource setup");

  const other = await context.newPage();
  await other.goto(base + "/durable/other");
  await other.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  assert.equal(await other.locator("#durable-count").textContent(), "0");
  assert.equal(await other.locator("#durable-draft").inputValue(), "My saved draft");
  await page.reload(); await connected();
  assert.equal(await page.locator("#durable-count").textContent(), "0");
  assert.equal(await page.locator("#durable-draft").inputValue(), "My saved draft");
  await control("/restart?worker=worker-a");
  assert.deepEqual(errors, []);
  console.log("  ok  another tab and page reload have independent fresh session identities");
  console.log("DURABLE STATE EXAMPLE BROWSER PASSED");
  await context.close();
} finally { await browser.close(); }
