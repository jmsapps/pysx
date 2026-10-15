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
  await page.addInitScript(() => {
    const Original = WebSocket;
    window.WebSocket = class extends Original {
      constructor(...args) {
        super(...args); window.socket = this;
        this.addEventListener("message", event => {
          const frame = JSON.parse(event.data);
          if (frame.owner) window.owner = frame.owner;
        });
      }
    };
  });
  const connected = async () => page.waitForFunction(() =>
    document.getElementById("pysx-root").dataset.pysxConnection === "connected", null, {timeout: 10000});
  const response = await page.goto(base + "/deep/link?owner=worker-a");
  assert.equal(response.status(), 200);
  assert.ok((await response.text()).includes("Linux durable deployment"));
  await connected();
  assert.equal(await page.evaluate(() => window.owner), "worker-a");
  await page.waitForFunction(() => document.getElementById("resource").textContent === "success");
  assert.ok(await page.evaluate(() => window.socket.url.startsWith("wss:")));
  assert.equal((await context.cookies()).find(cookie => cookie.name === "pysx_browser").secure, true);
  await page.locator("#increment").click();
  await page.waitForFunction(() => document.getElementById("count").textContent === "1");
  await page.locator("#delayed").click();
  await page.waitForFunction(() => document.getElementById("count").textContent === "11");
  console.log("  ok  Linux nginx TLS serves deep-link SSR, owner adoption, resources and async updates");

  const generation = await page.locator("#generation").textContent();
  await page.evaluate(() => { window.retained = document.getElementById("count"); window.socket.close(); });
  await connected();
  assert.equal(await page.locator("#generation").textContent(), generation);
  assert.equal(await page.evaluate(() => window.retained === document.getElementById("count")), true);
  await control("/drain?worker=worker-a");
  await page.locator("#increment").click();
  await page.waitForFunction(() => document.getElementById("count").textContent === "12");
  await control("/kill?worker=worker-a");
  await page.waitForFunction(generation =>
    document.getElementById("pysx-root").dataset.pysxConnection === "connected" &&
    document.getElementById("generation").textContent !== generation, generation, {timeout: 10000});
  assert.equal(await page.evaluate(() => window.owner), "worker-b");
  assert.equal(await page.locator("#count").textContent(), "12");
  await page.locator("#increment").click();
  await page.waitForFunction(() => document.getElementById("count").textContent === "13");
  console.log("  ok  live resume and draining preserve owners, then owner death reconstructs on Linux ASGI");

  await control("/restart?worker=worker-a");
  assert.equal((await context.request.get(base + "/_pysx/metrics")).status(), 404);
  assert.equal((await context.request.get(base + "/_fixture/drain")).status(), 404);
  assert.deepEqual(errors, []);
  console.log("  ok  restart restores readiness and the public proxy hides internal probes and controls");
  console.log("OPERATIONAL TOPOLOGY BROWSER PASSED");
  await context.close();
} finally { await browser.close(); }
