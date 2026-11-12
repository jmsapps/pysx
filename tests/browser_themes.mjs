import assert from "node:assert/strict";
import {mkdirSync, writeFileSync} from "node:fs";
import path from "node:path";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const pages = [];
const trace = [];
const record = (tab, event, detail) => {
  trace.push({time: Date.now(), tab, event, detail});
  if (trace.length > 200) trace.shift();
};
try {
  const page = await browser.newPage();
  const other = await browser.newPage();
  pages.push(page, other);
  for (const [tab, target] of pages.entries()) {
    target.on("pageerror", error => record(tab, "pageerror", error.message));
    target.on("websocket", socket => {
      record(tab, "socket-open", null);
      for (const direction of ["framesent", "framereceived"]) socket.on(direction, frame => {
        try {
          const message = JSON.parse(String(frame.payload));
          // Do not retain hello credentials, recovery capsules or document HTML.
          record(tab, direction, {t: message.t, h: message.h, seq: message.seq,
            status: message.status, operations: message.ops?.map(op => op.op)});
        } catch { record(tab, direction, "non-JSON frame"); }
      });
      socket.on("close", () => record(tab, "socket-close", null));
      socket.on("socketerror", error => record(tab, "socket-error", String(error)));
    });
    await target.addInitScript(() => {
      window.__themeTrace = [];
      const record = (event, detail) => {
        window.__themeTrace.push({time: Date.now(), event, detail});
        if (window.__themeTrace.length > 100) window.__themeTrace.shift();
      };
      document.addEventListener("click", event => record("click", {
        id: event.target.closest?.("[id]")?.id, trusted: event.isTrusted,
      }), true);
      window.addEventListener("pysx:connection", event => record("connection", {
        state: event.detail.state, attempt: event.detail.attempt,
      }));
    });
  }
  const address = `http://127.0.0.1:${process.argv[2]}`;
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await Promise.all([page.goto(address), other.goto(address)]);
  await Promise.all([page.waitForSelector("#preview"), other.waitForSelector("#preview")]);
  const sample = (target) => target.locator("#preview").evaluate(node => {
    const style = getComputedStyle(node);
    return [style.color, style.paddingLeft, style.borderLeftColor, style.borderLeftWidth];
  });
  assert.deepEqual(await sample(page), ["rgb(25, 35, 56)", "24px", "rgb(101, 84, 217)", "6px"]);
  const original = await page.locator("#preview").elementHandle();
  console.log("  ok  inherited layout and variable/theme snapshot");
  await page.locator("#dark").click();
  await page.waitForFunction(() => document.querySelector("#theme-name").textContent === "dark");
  assert.equal((await sample(page))[0], "rgb(249, 250, 251)");
  assert.equal(await other.locator("#theme-name").textContent(), "light");
  assert.equal((await sample(other))[0], "rgb(25, 35, 56)");
  console.log("  ok  two tabs switch themes without cross-talk");
  await page.locator("#compact").click();
  await page.waitForFunction(() => getComputedStyle(document.querySelector("#preview")).paddingLeft === "12px");
  await page.locator("#roomy").click();
  await page.waitForFunction(() => getComputedStyle(document.querySelector("#preview")).paddingLeft === "36px");
  await page.locator("#local-accent").click();
  await page.waitForFunction(() => document.querySelector("#preview").classList.contains("is-local"));
  assert.equal((await sample(page))[2], "rgb(13, 148, 136)");
  assert.equal(await original.evaluate(node => node === document.querySelector("#preview")), true);
  console.log("  ok  reactive variables and user class preserve the styled DOM node");
  await page.locator("#reset-vars").click();
  await page.waitForFunction(() => getComputedStyle(document.querySelector("#preview")).paddingLeft === "28px");
  assert.equal(await page.locator("#preview").evaluate(node => node.style.getPropertyValue("--preview-accent")), "");
  assert.equal((await sample(page))[2], "rgb(139, 92, 246)");
  await page.locator("#clear-theme").click();
  await page.waitForFunction(() => document.querySelector("#theme-name").textContent === "cleared");
  assert.equal((await sample(page))[0], "rgb(25, 35, 56)");
  console.log("  ok  reset removes variables and clear restores declared fallbacks");
  await page.setViewportSize({width: 375, height: 812});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.locator("#light").focus();
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.querySelector("#theme-name").textContent === "light");
  assert.equal(errors.length, 0);
  console.log("  ok  narrow layout and keyboard controls work without browser errors");
  console.log("THEMES BROWSER PASSED");
} catch (error) {
  const snapshots = await Promise.all(pages.map(page => page.evaluate(() => {
    const preview = document.querySelector("#preview");
    return {events: window.__themeTrace, visibility: document.visibilityState,
      focused: document.hasFocus(), active: document.activeElement?.id,
      connection: document.getElementById("pysx-root")?.dataset.pysxConnection,
      theme: document.querySelector("#theme-name")?.textContent,
      preview: preview && {padding: getComputedStyle(preview).paddingLeft,
        style: preview.getAttribute("style"), classes: preview.className}};
  }).catch(error => ({unavailable: error.message}))));
  const diagnostic = JSON.stringify({engine: process.env.PYSX_BROWSER_ENGINE,
    error: error.message, trace, snapshots});
  console.error(`PYSX THEMES FAILURE: ${diagnostic}`);
  if (process.env.PYSX_DEPLOYMENT_LOGS) {
    try {
      mkdirSync(process.env.PYSX_DEPLOYMENT_LOGS, {recursive: true});
      writeFileSync(path.join(process.env.PYSX_DEPLOYMENT_LOGS,
        `themes-${process.env.PYSX_BROWSER_ENGINE}-${process.pid}-${Date.now()}.json`), diagnostic);
    } catch (failure) { console.error(`Could not preserve themes diagnostics: ${failure.message}`); }
  }
  throw error;
} finally { await browser.close(); }
