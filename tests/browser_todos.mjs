// Browser acceptance for the todos port.
import playwright from "playwright";
const engine = playwright[process.env.PYSX_BROWSER_ENGINE ?? "chromium"];

const PORT = process.argv[2] ?? "8754";
const URL = `http://127.0.0.1:${PORT}/`;
const ok = (l) => console.log(`  ok  ${l}`);
const assert = (c, m) => { if (!c) throw new Error(m); };

const browser = await engine.launch();
const page = await browser.newPage();

let documentRequests = 0;
page.on("request", (r) => { if (r.resourceType() === "document") documentRequests++; });

try {
  await page.goto(URL);
  await page.waitForSelector("#todo-list li");

  const items = () => page.$$eval("#todo-list li", (els) =>
    els.map((el) => ({ key: el.dataset.pysxKey, done: el.className.includes("is-done") })));

  assert((await items()).length === 3, "expected 3 todos");
  assert((await page.textContent("p")).includes("2 items left"), "bad count text");
  ok("3 todos render, '2 items left'");

  // Stamp DOM identity on every item, then change exactly one.
  await page.$$eval("#todo-list li", (els) =>
    els.forEach((el, i) => { el.__probe = `probe${i}`; }));
  await page.click('li[data-pysx-key="1"] input[type=checkbox]');
  await page.waitForFunction(() =>
    document.querySelector('li[data-pysx-key="1"]').className.includes("is-done"));

  const probes = await page.$$eval("#todo-list li", (els) => els.map((el) => el.__probe ?? null));
  assert(probes[0] === null, "toggled item should have been replaced");
  assert(probes[1] === "probe1" && probes[2] === "probe2",
    `untouched items were rebuilt: ${JSON.stringify(probes)}`);
  ok("keyed reconcile: only the toggled node is replaced, siblings keep DOM identity");

  assert((await page.textContent("p")).includes("1 item left"), "count did not update");
  ok("count and plural updated through their own slots");

  // Caret must survive a bound input round trip.
  const field = 'input[type="text"]';
  await page.click(field);
  await page.keyboard.type("abc");
  // Home has different native editing semantics on macOS Firefox. Set the
  // same caret position explicitly, then exercise the real typing round trip.
  await page.$eval(field, (el) => el.setSelectionRange(0, 0));
  await page.keyboard.type("X");
  const state = await page.$eval(field, (el) => ({ v: el.value, caret: el.selectionStart }));
  assert(state.v === "Xabc", `value wrong: ${state.v}`);
  assert(state.caret === 1, `caret jumped to ${state.caret}; the server echoed the value back`);
  ok("typing mid-string keeps the caret (no echo from the server)");

  await page.click('button[type="submit"]');
  await page.waitForFunction(() => document.querySelectorAll("#todo-list li").length === 4);
  assert((await page.$eval(field, (el) => el.value)) === "", "input not cleared after submit");
  const added = (await items()).at(-1);
  assert(added.key === "4", `unexpected new key ${added.key}`);
  ok("submit appends the new todo and clears the field");

  const styleOf = (n) => page.$eval(`nav button:nth-child(${n})`, (el) => ({
    cls: el.getAttribute("class"),
    bg: getComputedStyle(el).backgroundColor,
    radius: getComputedStyle(el).borderRadius,
  }));
  const beforeClick = await styleOf(1);
  await page.click("nav button:nth-child(2)");          // Active
  await page.waitForFunction(() => document.querySelectorAll("#todo-list li").length === 2);
  const active = await items();
  assert(active.every((i) => !i.done), "Active filter showed a done item");
  ok("Active filter shows only undone todos");

  const afterClick = await styleOf(1);
  assert(/pysx-[0-9a-f]{6}/.test(afterClick.cls),
    `scoped class lost after click: ${afterClick.cls}`);
  assert(afterClick.radius === beforeClick.radius && afterClick.radius !== "0px",
    `styling died after click: ${JSON.stringify(afterClick)}`);
  const activeBtn = await styleOf(2);
  assert(activeBtn.bg !== afterClick.bg, "is-active styling not applied");
  ok("filter buttons keep their scoped styling across clicks");

  await page.click("nav button:nth-child(1)");          // All
  await page.waitForFunction(() => document.querySelectorAll("#todo-list li").length === 4);
  await page.click('li[data-pysx-key="3"] button');     // Remove
  await page.waitForFunction(() => !document.querySelector('li[data-pysx-key="3"]'));
  ok("remove deletes the right row (handler ids do not collide)");

  await page.click("button:has-text('Clear completed')");
  await page.waitForFunction(() =>
    [...document.querySelectorAll("#todo-list li")].every((el) => !el.className.includes("is-done")));
  ok("clear completed removes done todos");

  assert(documentRequests === 1, `page reloaded: ${documentRequests} document requests`);
  ok("no page reload throughout");

  console.log("TODOS BROWSER ACCEPTANCE PASSED");
} finally {
  await browser.close();
}
