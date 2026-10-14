import assert from "node:assert/strict";
import playwright from "playwright";
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const base = `http://127.0.0.1:${process.argv[2]}`;
try {
  const context = await browser.newContext();
  const writer = await context.newPage();
  const reader = await context.newPage();
  const sibling = await context.newPage();
  for (const [page, identity] of [[writer, "alice"], [reader, "reader"], [sibling, "alice"]]) {
    await page.goto(base + "/?principal=" + identity);
    await page.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
    assert.equal(await page.locator("#security-principal").textContent(), identity);
  }
  await writer.locator("#security-draft").fill("Writer-only draft");
  await writer.locator("#security-write").click();
  await writer.waitForFunction(() => document.getElementById("security-count").textContent === "1");
  assert.equal(await sibling.locator("#security-count").textContent(), "0");
  assert.equal(await sibling.locator("#security-draft").inputValue(), "My private draft");
  console.log("  ok  writer mutations and drafts stay within one tab even for the same identity");
  await reader.locator("#security-write").click();
  await reader.waitForFunction(() => document.getElementById("security-result").textContent.startsWith("Denied:"));
  assert.equal(await reader.locator("#security-count").textContent(), "0");
  await reader.locator("#security-draft").fill("Reader-only draft");
  await reader.waitForFunction(() => document.getElementById("security-echo").textContent === "Reader-only draft");
  assert.equal(await writer.locator("#security-draft").inputValue(), "Writer-only draft");
  console.log("  ok  read-only writes visibly fail while allowed private draft edits remain usable");
  await writer.locator("#choose-reader").click();
  await writer.waitForFunction(() => document.getElementById("pysx-root").dataset.pysxConnection === "connected");
  assert.equal(await writer.locator("#security-principal").textContent(), "reader");
  assert.equal(await writer.locator("#security-count").textContent(), "0");
  console.log("  ok  selecting another fixture identity renders a fresh principal-bound graph");
  console.log("SESSION SECURITY EXAMPLE BROWSER PASSED");
} finally { await browser.close(); }
