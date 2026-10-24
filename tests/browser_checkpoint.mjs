import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const context = await browser.newContext();
  const errors = [];
  await context.addInitScript(() => {
    const fail = () => { throw new Error("recovery must not write browser storage"); };
    Storage.prototype.setItem = fail;
    IDBFactory.prototype.open = fail;
    Object.defineProperty(document, "cookie", {get: () => "", set: fail});
    const Original = WebSocket;
    window.recoveryFrames = [];
    window.recoveryHellos = [];
    window.WebSocket = class extends Original {
      constructor(...args) {
        super(...args);
        window.recoverySocket = this;
        this.addEventListener("open", () => {
          const input = document.getElementById("recovery-draft");
          if (input && !window.recoveryHellos.length) input.value = "typed before attachment";
        });
        this.addEventListener("message", event => {
          try { window.recoveryFrames.push(JSON.parse(event.data)); } catch { /* Synthetic retired frames are ignored. */ }
        });
      }
      send(raw) {
        const message = JSON.parse(raw);
        if (message.t === "hello") window.recoveryHellos.push(message);
        return super.send(raw);
      }
    };
  });
  const page = await context.newPage();
  page.on("pageerror", error => errors.push(error.message));
  const url = `http://127.0.0.1:${process.argv[2]}/`;
  await page.goto(url);
  await page.waitForFunction(() => document.getElementById("pysx-root")?.dataset.pysxAttached === "true" ||
    window.recoverySocket?.readyState === WebSocket.CLOSED);
  if (await page.locator("#pysx-root").getAttribute("data-pysx-attached") !== "true") {
    const diagnostic = await page.evaluate(() => window.recoveryFrames.map(frame => ({
      t: frame.t, version: frame.version, keys: Object.keys(frame), reset: frame.reset,
      sameDocument: frame.document === window.recoveryHellos[0]?.document,
      capsuleLength: frame.capsule?.length, state: frame.state_rev, base: frame.base_rev,
      routing: frame.routing,
      tokenValid: /^[A-Za-z0-9_-]+={0,2}$/.test(frame.capsule),
    })));
    throw new Error(`attachment failed; page errors=${JSON.stringify(errors)}; frames=${JSON.stringify(diagnostic)}; status=${await page.locator("body").innerText()}`);
  }
  assert.equal(await page.locator("#recovery-draft").inputValue(), "typed before attachment");
  assert.equal(await page.evaluate(() => document.getElementById("pysx-root").hasAttribute("data-pysx-capsule") ||
    document.getElementById("pysx-root").hasAttribute("data-pysx-document")), false);
  await page.waitForFunction(() => window.recoveryFrames.some(frame => typeof frame.capsule === "string"));
  const first = await page.evaluate(() => window.recoveryFrames.filter(frame => frame.capsule).at(-1));
  assert.equal(first.version, 4);
  await page.click("#increment");
  await page.waitForFunction(() => document.getElementById("recovery-count").textContent === "1");
  const changed = await page.evaluate(() => window.recoveryFrames.filter(frame => frame.capsule).at(-1));
  assert.notEqual(changed.capsule, first.capsule);
  assert.equal(changed.t, "patch");
  await page.click("#hidden");
  await page.waitForFunction(previous => window.recoveryFrames.filter(frame => frame.capsule).at(-1).capsule !== previous, changed.capsule);
  const hidden = await page.evaluate(() => window.recoveryFrames.filter(frame => frame.capsule).at(-1));
  assert.equal(hidden.t, "ack");
  await page.click("#delayed");
  await page.waitForFunction(previous => window.recoveryFrames.filter(frame => frame.capsule).at(-1).capsule !== previous, hidden.capsule);
  await page.waitForFunction(() => window.recoveryFrames.some(frame => frame.t === "checkpoint" && frame.capsule));
  const latest = await page.evaluate(() => window.recoveryFrames.filter(frame => frame.capsule).at(-1));
  await page.evaluate(() => { window.retiredRecoverySocket = window.recoverySocket; window.recoverySocket.close(); });
  await page.waitForFunction(() => window.recoveryHellos.length >= 2 && document.getElementById("pysx-root").dataset.pysxAttached === "true");
  const resumed = await page.evaluate(() => window.recoveryHellos.at(-1));
  assert.equal(resumed.capsule, latest.capsule);
  assert.equal(resumed.document, latest.document);
  await page.evaluate(() => window.retiredRecoverySocket.dispatchEvent(new MessageEvent("message", {data: "invalid retired frame"})));
  assert.equal(await page.locator("#recovery-count").textContent(), "1");
  const tab = await context.newPage();
  await tab.goto(url);
  await tab.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "true");
  assert.equal(await tab.locator("#recovery-count").textContent(), "0");
  assert.notEqual(await tab.evaluate(() => window.recoveryHellos[0].document), resumed.document);
  await page.reload();
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "true");
  assert.equal(await page.locator("#recovery-count").textContent(), "0");
  assert.notEqual(await page.evaluate(() => window.recoveryHellos[0].document), resumed.document);
  assert.deepEqual(errors, []);
  console.log("  ok  HTTP adoption, hidden and async state publish without browser storage");
  console.log("  ok  one current capsule survives live resume; retired sockets, new tabs and refresh are isolated");

  const mocked = await context.newPage();
  let route;
  let closed = null;
  const sent = [];
  await mocked.routeWebSocket("**/ws?*", socket => {
    route = socket;
    socket.onMessage(raw => sent.push(JSON.parse(raw)));
    socket.onClose(code => { closed = code; });
  });
  await mocked.goto(url);
  for (let attempt = 0; !sent.length && attempt < 100; attempt++) await mocked.waitForTimeout(20);
  assert.equal(sent[0]?.t, "hello");
  const doc = sent[0].document;
  const frame = (base, extra) => ({version: 4, epoch: "publication", base_rev: base,
    state_rev: base + 1, ...extra});
  route.send(JSON.stringify(frame(0, {t: "init", html: '<pysx-slot id="current">0</pysx-slot>', css: "",
    document: doc, capsule: "first", reset: ""})));
  await mocked.waitForSelector("#current");
  route.send(JSON.stringify(frame(2, {t: "patch", ops: [{op: "text", id: "current", v: "lost predecessor"}],
    document: doc, capsule: "out_of_order", reset: ""})));
  route.send(JSON.stringify(frame(2, {t: "patch", ops: [{op: "text", id: "current", v: "duplicate lost predecessor"}],
    document: doc, capsule: "duplicate_out_of_order", reset: ""})));
  await mocked.waitForTimeout(50);
  assert.equal(await mocked.locator("#current").textContent(), "0");
  assert.equal(sent.filter(message => message.t === "repair").length, 1);
  route.send(JSON.stringify(frame(3, {t: "repair", html: '<pysx-slot id="current">settled</pysx-slot>', css: "",
    expected: 1, document: doc, capsule: "repaired", reset: ""})));
  await mocked.waitForFunction(() => document.getElementById("current").textContent === "settled");
  route.send(JSON.stringify(frame(4, {t: "checkpoint", document: doc, capsule: "navigated", reset: "",
    navigations: [{t: "navigation", url: "/checkpoint-route", rev: 0, mode: "push"}]})));
  await mocked.waitForURL("**/checkpoint-route");
  route.send(JSON.stringify(frame(5, {t: "patch", ops: [{op: "text", id: "current", v: "must not apply"}],
    document: doc, capsule: "bad_token\n", reset: ""})));
  for (let attempt = 0; closed === null && attempt < 100; attempt++) await mocked.waitForTimeout(20);
  assert.equal(closed, 4002);
  assert.equal(await mocked.locator("#current").textContent(), "settled");
  console.log("  ok  gaps repair, navigation commits with its capsule, and malformed capsules reject the entire frame");
  console.log("BROWSER RECOVERY PASSED");
} finally { await browser.close(); }
