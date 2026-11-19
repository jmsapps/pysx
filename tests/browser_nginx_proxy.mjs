import assert from "node:assert/strict";
import playwright from "playwright";

const port = process.argv[2];
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  // The deployment uses disposable TLS, so the certificate is deliberately untrusted.
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();

  await page.goto(`https://localhost:${port}/`);
  await page.waitForFunction(() => document.querySelector("#pysx-root")?.dataset.pysxAttached === "true");
  console.log("  ok  the application attaches over a proxied TLS connection");

  // Events must survive the WebSocket upgrade through nginx, not merely the first render.
  // Value renders as a <span> inside the Metric paragraph.
  const value = page.locator("#pysx-root p span").first();
  const before = (await value.textContent()).trim();
  const button = page.getByRole("button", { name: "Count a click" });
  assert.equal(await button.count(), 1, "the example renders its button");
  await button.click();
  await page.waitForFunction(
    (previous) => document.querySelector("#pysx-root p span")?.textContent?.trim() !== previous,
    before, { timeout: 30000 });
  console.log("  ok  events round-trip through the proxied websocket");

  // Cookies are marked secure because the application sees the forwarded protocol.
  const cookies = await context.cookies();
  assert(cookies.length, "the deployment sets at least one cookie");
  assert(cookies.every((cookie) => cookie.secure), `every cookie is secure: ${JSON.stringify(cookies)}`);
  console.log("  ok  forwarded protocol produces secure cookies");

  // The mounted application resolves under its prefix because the full path is proxied
  // with no replacement URI. Its server-rendered document is asserted here; browser
  // attachment for a mounted application is an unfinished check recorded in the task
  // evidence, not something this suite silently claims.
  const mounted = await page.goto(`https://localhost:${port}/reports`);
  assert.equal(mounted.status(), 200, "the mounted application is served under its prefix");
  assert.match(await page.locator("#pysx-root").textContent(), /path prefix/i);
  console.log("  ok  the mounted application is served under its prefix");

  console.log("NGINX PROXY BROWSER PASSED");
} finally {
  await browser.close();
}
