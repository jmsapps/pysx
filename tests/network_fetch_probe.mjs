// Diagnostic only: compare caught network failures with browser pageerror events.
import assert from "node:assert/strict";
import {createServer} from "node:http";
import playwright from "playwright";

if (process.platform === "darwin") delete process.env.DYLD_LIBRARY_PATH;
for (const engine of ["chromium", "firefox", "webkit"]) {
  const browser = await playwright[engine].launch();
  const samples = [];
  try {
    for (let cycle = 0; cycle < 20; cycle++) {
      const phase = cycle % 2 ? "partial-json" : "before-headers";
      let reached;
      const requested = new Promise(resolve => { reached = resolve; });
      const server = createServer((request, response) => {
        if (request.url === "/status") {
          if (phase === "partial-json") {
            response.writeHead(200, {"Content-Type": "application/json"});
            response.write('{"value":');
          }
          reached();
          return;
        }
        response.end("<!doctype html><title>fetch interruption</title>");
      });
      await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
      const page = await browser.newPage();
      const errors = [];
      const failures = [];
      page.on("pageerror", error => errors.push({name: error.name, message: error.message}));
      page.on("requestfailed", request => failures.push(request.failure()?.errorText));
      try {
        await page.goto(`http://127.0.0.1:${server.address().port}`);
        const result = page.evaluate(async () => {
          try {
            const response = await fetch("/status", {signal: AbortSignal.timeout(5000)});
            await response.json();
            return "unexpected success";
          } catch (error) { return `caught:${error.name}`; }
        });
        await Promise.race([requested, result.then(value => {
          throw new Error(`fetch ended before server interruption: ${value}`);
        })]);
        server.closeAllConnections();
        await new Promise(resolve => server.close(resolve));
        const caught = await result;
        assert(caught.startsWith("caught:"), caught);
        await page.waitForTimeout(50);
        samples.push({phase, caught, errors, failures});
      } finally {
        server.closeAllConnections(); server.close();
        await page.close();
      }
    }
  } finally { await browser.close(); }
  console.log(JSON.stringify({engine, cases: samples.length,
    page_error_cases: samples.filter(sample => sample.errors.length).length, samples}));
}
