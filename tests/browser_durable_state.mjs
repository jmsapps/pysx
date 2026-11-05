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
  // A bare waitForFunction timeout says only that the state never arrived, which
  // is why a remote failure here is unreproducible. The client already announces
  // every transition, so they are recorded and reported with the failure instead.
  await context.addInitScript(() => {
    window.__pysxTransitions = [];
    window.addEventListener("pysx:connection", event => window.__pysxTransitions.push(
      `${Math.round(performance.now())}ms ${event.detail.state}/attempt-${event.detail.attempt}`));
  });
  const diagnose = async target => {
    const read = expression => target.evaluate(expression).catch(() => "unavailable");
    return `connection ${await read(() => document.getElementById("pysx-root")?.dataset.pysxConnection)}`
      + `, transitions: ${await read(() => (window.__pysxTransitions ?? []).join(" -> ") || "none")}`;
  };
  // One failed attachment costs the client a 5s deadline plus its backoff before
  // a second attempt even opens, so a budget under that cannot survive the single
  // retry the transport is built to make.
  const settle = async (target, predicate, argument = null) => target.waitForFunction(
    predicate, argument, {timeout: 20000},
  ).catch(async error => { throw new Error(`${error.message}\n${await diagnose(target)}`); });
  const connected = async (target = page) => settle(target, () =>
    document.getElementById("pysx-root").dataset.pysxConnection === "connected");
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
  await settle(page, generation => document.getElementById("pysx-root").dataset.pysxConnection === "connected" &&
    document.getElementById("durable-generation").textContent !== generation, generation);
  assert.equal(await page.locator("#durable-count").textContent(), "1");
  assert.equal(await page.locator("#durable-draft").inputValue(), "A draft preserved across worker loss");
  await page.waitForFunction(() => document.getElementById("durable-resource").textContent === "Resource rebuilt");
  assert.equal(await page.locator("#pysx-connection-status").count(), 0);
  await page.locator("#durable-increment").click();
  await page.waitForFunction(() => document.getElementById("durable-count").textContent === "2");
  console.log("  ok  worker loss preserves committed typed state and rebuilds working handlers and resource setup");

  const other = await context.newPage();
  await other.goto(base + "/durable/other");
  await connected(other);
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
