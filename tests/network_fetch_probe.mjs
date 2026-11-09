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
      const phase = ["before-headers", "partial-json", "navigation", "navigation-abort"][cycle % 4];
      let reached;
      const requested = new Promise(resolve => { reached = resolve; });
      let documents = 0;
      const server = createServer((request, response) => {
        if (request.url === "/status") {
          if (phase === "partial-json") {
            response.writeHead(200, {"Content-Type": "application/json"});
            response.write('{"value":');
          }
          reached();
          return;
        }
        const script = documents++ === 0 ? `<script>
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 5000);
          if (${phase === "navigation-abort"}) {
            addEventListener("pagehide", () => controller.abort(), {once: true});
          }
          setTimeout(async () => {
            try {
              const response = await fetch("/status", {signal: controller.signal});
              await response.json();
              window.fetchResult = "unexpected success";
            } catch (error) { window.fetchResult = "caught:" + error.name; }
            finally { clearTimeout(timer); }
          }, 0);
        </script>` : "";
        response.end("<!doctype html><title>fetch interruption</title>" + script);
      });
      await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
      const page = await browser.newPage();
      const errors = [];
      const failures = [];
      page.on("pageerror", error => errors.push({name: error.name, message: error.message}));
      page.on("requestfailed", request => failures.push(request.failure()?.errorText));
      try {
        await page.goto(`http://127.0.0.1:${server.address().port}`);
        await Promise.race([requested, page.waitForTimeout(6000).then(() => {
          throw new Error("page script did not issue its status request");
        })]);
        let caught;
        if (phase.startsWith("navigation")) {
          await page.reload();
          caught = "context:navigation";
        } else {
          server.closeAllConnections();
          await new Promise(resolve => server.close(resolve));
          await page.waitForFunction(() => window.fetchResult, null, {timeout: 7000});
          caught = await page.evaluate(() => window.fetchResult);
        }
        assert(caught.startsWith("caught:") ||
          (phase.startsWith("navigation") && caught.startsWith("context:")), caught);
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
