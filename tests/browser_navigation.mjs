import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  const errors = [];
  const messages = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("websocket", socket => socket.on("framesent", frame => {
    try { messages.push(JSON.parse(frame.payload)); } catch { /* Binary frames are irrelevant. */ }
  }));
  await page.goto(`http://127.0.0.1:${process.argv[2]}/?initial=yes#native`);
  const route = async path => {
    // Rendering and committing browser history are separate ordered messages.
    await page.waitForFunction(path => document.querySelector("#route-path")?.textContent === path && location.pathname === path, path);
    assert.equal(new URL(page.url()).pathname, path);
  };
  const text = async (id, value) => page.waitForFunction(
    ([id, value]) => document.getElementById(id)?.textContent === value, [id, value]);
  await route("/");
  await text("search", "?initial=yes"); await text("hash", "#native");
  await page.locator("#one").click();
  await route("/users/1");
  await text("calls", "1");
  await text("search", "?old=yes");
  await text("hash", "#old");
  await page.locator("#increment").click();
  await text("increment", "1");
  await page.locator("#live").click();
  await route("/users/2");
  await text("param", "2");
  await text("increment", "1");
  await text("search", ""); await text("hash", "");
  await page.locator("#change-href").click();
  await page.waitForFunction(() => document.querySelector("#live").getAttribute("href") === "/users/3");
  await page.locator("#live").click();
  await route("/users/3");
  console.log("  ok  isolated route params, retained state, live href and composed handlers");

  await page.locator("#one").click(); await route("/users/1");
  await page.locator("#descend").click(); await route("/users/1/edit");
  await text("search", "?mode=raw"); await text("hash", "");
  await page.locator("#sibling").click(); await route("/users/1/settings");
  await page.locator("#parent").click(); await route("/users/1/");
  await page.locator("#dot-child").click(); await route("/users/1/edit");
  await page.locator("#grandparent").click(); await route("/users/");
  await page.locator("#grandparent").click(); await route("/");
  await page.locator("#parent").click(); await route("/");
  await page.locator("#one").click(); await route("/users/1");
  await page.locator("#root").click(); await route("/");
  await page.locator("#wildcard").click(); await route("/files/a/b/");
  console.log("  ok  filesystem children, parents, root and wildcard trailing slash matches");

  const beforeCancel = page.url();
  const calls = Number(await page.locator("#calls").textContent());
  await page.locator("#cancel").click(); await text("calls", String(calls + 1));
  assert.equal(page.url(), beforeCancel);
  await page.locator("#async").click(); await route("/users/8");
  await text("calls", String(calls + 2));
  await page.locator("#programmatic").click(); await route("/users/4");
  await page.goBack(); await route("/files/a/b/");
  await page.goForward(); await route("/users/4");
  console.log("  ok  explicit cancellation, awaited handlers, push/replace and back/forward");

  const locationCount = messages.filter(message => message.t === "location").length;
  await page.locator("#fragment").click(); await text("hash", "#native");
  assert.equal(new URL(page.url()).hash, "#native");
  assert.equal(messages.filter(message => message.t === "location").length, locationCount + 1);
  // Observe native defaults at document before preventing real navigation in the test.
  await page.evaluate(() => {
    window.nativeResults = [];
    document.addEventListener("click", event => {
      if (["download", "blank", "external", "live"].includes(event.target.closest("a")?.id)) {
        window.nativeResults.push(event.defaultPrevented);
        event.preventDefault();
      }
    });
  });
  const beforeNative = page.url();
  for (const id of ["download", "blank", "external"]) await page.locator(`#${id}`).click();
  for (const modifier of ["altKey", "ctrlKey", "metaKey", "shiftKey"]) {
    await page.locator("#live").dispatchEvent("click", {[modifier]: true, button: 0});
  }
  assert.deepEqual(await page.evaluate(() => window.nativeResults), Array(7).fill(false));
  for (const href of ["", "mailto:a@example.test", "tel:123", "sms:123", "//example.invalid/x"]) {
    await page.evaluate(href => document.querySelector("#external").setAttribute("href", href), href);
    await page.locator("#external").click();
  }
  await page.evaluate(() => {
    const link = document.querySelector("#live");
    link.addEventListener("click", event => event.preventDefault(), {once: true});
    link.click();
    link.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true, button: 1}));
  });
  assert.deepEqual(await page.evaluate(() => window.nativeResults.slice(-2)), [true, false]);
  assert.equal(page.url(), beforeNative);
  assert.deepEqual(errors, []);
  console.log("  ok  fragment observation deduplicates and download/target/external/modifier clicks stay native");

  const reloaded = await browser.newPage();
  const reloadErrors = [];
  reloaded.on("pageerror", error => reloadErrors.push(error.message));
  const deepResponse = await reloaded.goto(`http://127.0.0.1:${process.argv[2]}/users/7?tab=a#part`);
  assert.equal(deepResponse.status(), 200);
  await reloaded.waitForFunction(() => document.querySelector("#route-path")?.textContent === "/users/7");
  await reloaded.waitForFunction(() => document.getElementById("param")?.textContent === "7");
  await reloaded.waitForFunction(() => document.getElementById("search")?.textContent === "?tab=a");
  assert.equal(new URL(reloaded.url()).pathname, "/users/7");
  await reloaded.locator("#root").click();
  await reloaded.waitForFunction(() => document.querySelector("#route-path")?.textContent === "/");
  assert.deepEqual(reloadErrors, []);
  await reloaded.close();
  console.log("  ok  a reloaded deep link serves the shell and renders its route");
  console.log("NAVIGATION BROWSER PASSED");
} finally { await browser.close(); }
