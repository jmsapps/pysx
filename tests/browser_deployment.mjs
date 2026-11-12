import assert from "node:assert/strict";
import {mkdirSync, writeFileSync} from "node:fs";
import path from "node:path";
import playwright from "playwright";

const controls = `http://127.0.0.1:${process.argv[2]}`;
const control = async path => {
  const response = await fetch(controls + path);
  assert.equal(response.status, 200);
  return response.json();
};
const {base} = await control("/reset");
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
let page;
const errors = [];
const trace = [];
const record = (event, detail) => {
  trace.push({time: Date.now(), event, ...detail});
  if (trace.length > 200) trace.shift();
};
try {
  const context = await browser.newContext({ignoreHTTPSErrors: true});
  page = await context.newPage();
  page.on("pageerror", error => errors.push(error.message));
  let socketId = 0;
  page.on("websocket", socket => {
    const id = ++socketId;
    record("socket-created", {id});
    for (const direction of ["framesent", "framereceived"]) socket.on(direction, frame => {
      try {
        const message = JSON.parse(String(frame.payload));
        // Allowlist metadata: never retain credentials, capsules or HTML.
        record(direction, {id, t: message.t, version: message.version,
          epoch: message.epoch, state: message.state_rev, base: message.base_rev,
          owner: message.owner, status: message.status});
      } catch { record(direction, {id, malformed: true}); }
    });
    socket.on("socketerror", error => record("socket-error", {id, error: String(error)}));
    socket.on("close", () => record("socket-closed", {id}));
  });
  await page.addInitScript(() => {
    window.deploymentTrace = [];
    const record = (event, detail) => {
      window.deploymentTrace.push({time: Date.now(), event, ...detail});
      if (window.deploymentTrace.length > 200) window.deploymentTrace.shift();
    };
    window.addEventListener("pysx:connection", event => record("connection", {
      state: event.detail.state, attempt: event.detail.attempt,
    }));
    let socketId = 0;
    const Original = WebSocket;
    window.WebSocket = class extends Original {
      addEventListener(type, listener, options) {
        if (type !== "close") return super.addEventListener(type, listener, options);
        return super.addEventListener(type, event => {
          if (this.delayedClose) setTimeout(() => listener.call(this, event), 1000);
          else listener.call(this, event);
        }, options);
      }
      close(...args) {
        this.delayedClose = window.delayDeploymentClose === true;
        window.delayDeploymentClose = false;
        return super.close(...args);
      }
      constructor(...args) {
        super(...args); window.socket = this;
        const id = ++socketId;
        record("socket-created", {id});
        // Observe native close immediately, independent of the delayed listener fixture.
        super.addEventListener("open", () => record("socket-open", {id}));
        super.addEventListener("error", () => record("socket-error", {id}));
        super.addEventListener("close", event => record("socket-close", {
          id, code: event.code, reason: event.reason, clean: event.wasClean,
        }));
        this.addEventListener("message", event => {
          const frame = JSON.parse(event.data);
          if (frame.owner) window.owner = frame.owner;
        });
      }
    };
  });
  const connected = async (replacement = false) => {
    try {
      return await page.waitForFunction(replacement =>
        document.getElementById("pysx-root").dataset.pysxConnection === "connected" &&
        (!replacement || (window.socket !== window.disconnectedSocket &&
          window.socket.readyState === WebSocket.OPEN)), replacement, {timeout: 20000});
    } catch (error) {
      const state = await page.evaluate(() => {
        const root = document.getElementById("pysx-root");
        return root ? root.dataset.pysxConnection ?? "unset" : "no-root";
      }).catch(() => "unreadable");
      throw new Error(`deployment never reached connected; last connection state: ${state}\n${error.message}`);
    }
  };
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
  await page.evaluate(() => {
    window.retained = document.getElementById("count");
    window.delayDeploymentClose = true;
    window.disconnectedSocket = window.socket;
    window.socket.close();
  });
  await connected(true);
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
} catch (error) {
  const state = await page?.evaluate(() => ({
    connection: document.getElementById("pysx-root")?.dataset.pysxConnection,
    count: document.getElementById("count")?.textContent,
    generation: document.getElementById("generation")?.textContent,
    readyState: window.socket?.readyState,
    sameSocket: window.socket === window.disconnectedSocket,
    owner: window.owner,
    timeline: window.deploymentTrace,
  })).catch(() => null);
  const diagnostic = JSON.stringify({engine: process.env.PYSX_BROWSER_ENGINE,
    error: error.message, state, errors, trace});
  console.error(`DEPLOYMENT FAILURE: ${diagnostic}`);
  if (process.env.PYSX_DEPLOYMENT_LOGS) {
    try {
      mkdirSync(process.env.PYSX_DEPLOYMENT_LOGS, {recursive: true});
      writeFileSync(path.join(process.env.PYSX_DEPLOYMENT_LOGS,
        `deployment-browser-${process.env.PYSX_BROWSER_ENGINE}-${process.pid}-${Date.now()}.json`), diagnostic);
    } catch (failure) { console.error(`Could not preserve deployment diagnostics: ${failure.message}`); }
  }
  throw error;
} finally { await browser.close(); }
