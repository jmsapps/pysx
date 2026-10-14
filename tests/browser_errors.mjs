import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const failures = [];
  page.on("pageerror", error => failures.push(error.message));
  let connection;
  await page.routeWebSocket("**/ws?*", socket => { connection = socket; });
  await page.goto(`http://127.0.0.1:${process.argv[2]}/`);
  await page.waitForFunction(() => document.readyState === "complete");
  await page.evaluate(() => {
    window.outcomes = [];
    window.addEventListener("pysx:result", event => window.outcomes.push({
      ...event.detail, text: document.getElementById("value").textContent,
    }));
  });
  assert.ok(connection);
  let revision = 0;
  const send = message => connection.send(JSON.stringify({...message, version: 3, epoch: "test", base_rev: revision, state_rev: ++revision}));
  send({t: "init", html: '<pysx-slot data-pysx="value" id="value">0</pysx-slot>', css: ""});
  const error = {id: "correlation", owner: "generation", phase: "handler", message: "Please try again."};
  for (const [status, text] of [["error", "1"], ["success", "2"], ["invalid", "2"], ["stale", "2"]]) {
    send({t: "result", h: "h0", status,
      ops: [{op: "text", id: "value", v: text}], ...(status === "error" ? {error} : {})});
  }
  await page.waitForFunction(() => window.outcomes.length === 4);
  assert.deepEqual(await page.evaluate(() => window.outcomes), [
    {handler: "h0", status: "error", error, text: "1"},
    {handler: "h0", status: "success", error: null, text: "2"},
    {handler: "h0", status: "invalid", error: null, text: "2"},
    {handler: "h0", status: "stale", error: null, text: "2"},
  ]);
  assert.deepEqual(failures, []);
  console.log("  ok  repair commits before error acknowledgement and success clears the error");
  console.log("  ok  invalid and stale outcomes stay distinct without uncaught client errors");
  console.log("BOUNDARY ERROR BROWSER PASSED");
} finally { await browser.close(); }
