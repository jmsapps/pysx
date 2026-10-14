import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${process.argv[2]}/`);
  await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxAttached === "true");
  const result = await page.evaluate(async () => {
    const socket = new WebSocket(`ws://${location.host}/ws?protocol=3`);
    const frames = [];
    let epoch, rev;
    const wait = predicate => new Promise((resolve, reject) => {
      const timeout = setTimeout(() => { clearInterval(timer); reject(new Error("frame timeout")); }, 4000);
      const timer = setInterval(() => { if (predicate()) { clearTimeout(timeout); clearInterval(timer); resolve(); } }, 5);
    });
    socket.onopen = () => socket.send(JSON.stringify({t: "hello", version: 3, edits: []}));
    socket.onmessage = event => {
      const frame = JSON.parse(event.data); frames.push(frame);
      epoch = frame.epoch; rev = frame.state_rev;
    };
    await wait(() => frames.some(frame => frame.t === "init"));
    const first = frames.find(frame => frame.t === "init");
    const doc = new DOMParser().parseFromString(first.html, "text/html");
    const handler = doc.querySelector("[data-pysx-click]").getAttribute("data-pysx-click");
    const event = seq => ({t: "event", version: 3, epoch, state_rev: rev, seq, h: handler});
    socket.send(JSON.stringify(event(1)));
    await wait(() => frames.some(frame => frame.t === "ack"));
    socket.send(JSON.stringify(event(1)));
    await wait(() => frames.some(frame => frame.status === "duplicate"));
    socket.send(JSON.stringify(event(3)));
    await wait(() => frames.some(frame => frame.t === "repair"));
    socket.send("null"); socket.send(JSON.stringify({t: "event", h: 1}));
    await wait(() => frames.filter(frame => frame.status === "invalid").length === 2);
    socket.close();
    return {statuses: frames.filter(frame => frame.t === "ack").map(frame => frame.status),
      repair: frames.find(frame => frame.t === "repair").html,
      revisions: frames.map(frame => frame.state_rev)};
  });
  assert.deepEqual(result.statuses, ["success", "duplicate", "gap"]);
  assert.match(result.repair, />1</);
  assert.deepEqual(result.revisions, result.revisions.map((_, index) => index + 1));
  assert.deepEqual(errors, []);
  console.log("  ok  negotiated epoch rejects duplicate/gap events and repairs actual state");
  console.log("  ok  invalid shapes are acknowledged without invoking handlers");
  const repairPage = await browser.newPage();
  repairPage.on("pageerror", error => errors.push(error.message));
  let mock;
  let resolveClose;
  const closed = new Promise(resolve => { resolveClose = resolve; });
  const requests = [];
  await repairPage.routeWebSocket("**/ws?*", route => {
    mock = route;
    route.onMessage(raw => requests.push(JSON.parse(raw)));
    route.onClose(code => resolveClose(code));
  });
  await repairPage.goto(`http://127.0.0.1:${process.argv[2]}/`);
  await repairPage.waitForFunction(() => document.readyState === "complete");
  const html = '<input id="draft" data-pysx-el="stable" value="draft"><pysx-slot id="value">0</pysx-slot>';
  mock.send(JSON.stringify({t: "init", version: 3, epoch: "mock", base_rev: 0, state_rev: 1, html, css: ""}));
  await repairPage.waitForSelector("#draft");
  await repairPage.locator("#draft").evaluate(el => { window.saved = el; el.focus(); el.setSelectionRange(2, 2); });
  mock.send(JSON.stringify({t: "patch", version: 3, epoch: "mock", base_rev: 5, state_rev: 6, ops: [{op: "text", id: "value", v: "discontinuous"}]}));
  await repairPage.waitForTimeout(30);
  assert.equal(await repairPage.locator("#value").textContent(), "0");
  assert.equal(requests.filter(message => message.t === "repair").length, 1);
  mock.send(JSON.stringify({t: "repair", version: 3, epoch: "mock", base_rev: 6, state_rev: 7, expected: 1, html: html.replace(">0<", ">current<"), css: ""}));
  await repairPage.waitForFunction(() => document.getElementById("value").textContent === "current");
  assert.equal(await repairPage.evaluate(() => window.saved === document.getElementById("draft") && document.activeElement === window.saved && window.saved.selectionStart === 2), true);
  console.log("  ok  client revision gaps repair through retained identities without losing focus");
  await repairPage.evaluate(() => {
    document.activeElement.blur();
    const text = document.getElementById("value").firstChild;
    window.getSelection().setBaseAndExtent(text, 2, text, 7);
  });
  mock.send(JSON.stringify({t: "repair", version: 3, epoch: "mock", base_rev: 7, state_rev: 8, expected: 1, html: html.replace(">0<", ">x<"), css: ""}));
  await repairPage.waitForFunction(() => document.getElementById("value").textContent === "x");
  assert.equal(await repairPage.evaluate(() => window.getSelection().focusOffset), 1);
  mock.send(JSON.stringify({t: "patch", version: 3, epoch: "mock", base_rev: 8, state_rev: 9, ops: [{op: "children", id: "value", v: "bad"}]}));
  const closeTimeout = setTimeout(() => resolveClose("timeout"), 3000);
  assert.equal(await closed, 4002);
  clearTimeout(closeTimeout);
  assert.equal(await repairPage.locator("#value").textContent(), "x");
  assert.deepEqual(errors, []);
  console.log("  ok  shortened selection is clamped and malformed server operations close safely");
  console.log("SHAPE PROTOCOL BROWSER PASSED");
} finally { await browser.close(); }
