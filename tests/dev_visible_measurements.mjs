import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { performance } from "node:perf_hooks";
import { chromium } from "playwright";

const [port, filename, kind] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;
const browser = await chromium.launch();
const samples = [];
try {
  const page = await browser.newPage();
  await page.addInitScript(() => {
    window.measureConnections = [];
    addEventListener("pysx:disconnected", () => { window.measureDisconnected = performance.now(); });
    addEventListener("pysx:connection", event => {
      window.measureConnections.push({...event.detail, at: performance.now()});
    });
  });
  await page.goto(base);
  const connected = () => page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  await connected();
  const renderedSpans = await page.locator("#pysx-root span").count();
  if (kind === "large") assert.ok(renderedSpans >= 1500, `large workload rendered only ${renderedSpans} spans`);
  const countSelector = kind === "counter" ? 'pysx-slot[id="0"]' : '#measure-count';
  const expectedCount = kind === "counter" ? "1" : "8";
  await page.getByRole("button", {name: "Increment", exact: true}).click();
  await page.waitForFunction(({selector, value}) => document.querySelector(selector).textContent === value, {selector: countSelector, value: expectedCount});
  const original = readFileSync(filename, "utf8");
  for (let run = 1; run <= 5; run++) {
    const marker = kind === "counter" ? `Counter speed ${run}` : `build-${run}`;
    const source = kind === "counter" ? original.replace('Title: "Counter"', `Title: "${marker}"`) : original.replaceAll("build-0", marker);
    assert.notEqual(source, original);
    await page.evaluate(() => { window.measureConnections = []; window.measureDisconnected = null; });
    const writtenAt = Date.now() / 1000;
    const started = performance.now();
    writeFileSync(filename, source);
    await page.waitForFunction(({kind, marker}) => kind === "counter" ? document.querySelector("h1").textContent === marker : document.getElementById("pysx-root").textContent.includes(marker), {kind, marker}, {polling: "raf"});
    const visibleSeconds = (performance.now() - started) / 1000;
    await connected();
    assert.equal(await page.locator(countSelector).textContent(), expectedCount);
    const status = await (await page.request.get(base + "/__pysx_dev__/status")).json();
    const connections = await page.evaluate(() => ({events: window.measureConnections, disconnected: window.measureDisconnected}));
    assert.ok(connections.events.filter(event => event.state === "reconnecting").every(event => event.retryMs === 0));
    const attached = connections.events.findLast(event => event.state === "connected");
    assert.ok(attached && connections.disconnected !== null);
    samples.push({run, source_sha256: createHash("sha256").update(source).digest("hex"),
      visible_seconds: visibleSeconds, detection_seconds: status.detected_at - writtenAt,
      attachment_seconds: (attached.at - connections.disconnected) / 1000,
      reload_seconds: status.reload_seconds, import_seconds: status.import_seconds,
      stages: status.stages, count: expectedCount, workers: status.workers.length,
      connection_events: connections.events});
  }
  console.log(JSON.stringify({kind, endpoint: "source write to RAF DOM text observation", browser: browser.version(), rendered_spans: renderedSpans, samples}));
} finally { await browser.close(); }
