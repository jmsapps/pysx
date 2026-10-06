// Browser half of the acceptance test (PLAN.md §1).
import playwright from "playwright";
const engine = playwright[process.env.PYSX_BROWSER_ENGINE ?? "chromium"];

const PORT = process.argv[2] ?? "8752";
const URL = `http://127.0.0.1:${PORT}/`;

const ok = (label) => console.log(`  ok  ${label}`);
const assert = (cond, msg) => { if (!cond) { throw new Error(msg); } };

const browser = await engine.launch();
const context = await browser.newContext();

try {
  const one = await context.newPage();
  const two = await context.newPage();

  let documentRequests = 0;
  one.on("request", (r) => { if (r.resourceType() === "document") documentRequests++; });

  await one.goto(URL);
  await two.goto(URL);
  await one.waitForSelector("pysx-slot");
  await two.waitForSelector("pysx-slot");

  const read = (p) => p.textContent("#pysx-root");
  assert((await read(one)).includes("Count: 0"), "tab 1 did not start at 0");
  assert((await read(two)).includes("Count: 0"), "tab 2 did not start at 0");
  ok("both tabs render Count: 0");

  const attrs = await one.$eval("button", (b) => ({
    click: b.getAttribute("data-pysx-click"),
    onclick: b.getAttribute("onclick"),
    cls: b.getAttribute("class"),
  }));
  assert(attrs.click === "h1", `data-pysx-click missing: ${JSON.stringify(attrs)}`);
  assert(attrs.onclick === null, `onclick present: ${JSON.stringify(attrs)}`);
  assert(/^pysx-[0-9a-f]{16}$/.test(attrs.cls), `bad styled class: ${attrs.cls}`);
  ok("button carries a real data-pysx-click attribute, no onclick");

  const styleText = await one.$eval("#pysx-style", (s) => s.textContent);
  const rules = [...styleText.matchAll(/\.pysx-[0-9a-f]{16}\s*\{/g)].map((m) => m[0]);
  const usedClasses = await one.$$eval("#pysx-root [class]", (elements) =>
    [...new Set(elements.flatMap((element) => [...element.classList]))]
      .filter((name) => /^pysx-[0-9a-f]{16}$/.test(name)));
  assert(usedClasses.length >= 2, "missing styled page or controls");
  for (const cls of usedClasses) {
    assert(rules.filter((rule) => rule.startsWith(`.${cls} `)).length === 1,
      `missing or duplicate scoped rule: ${cls}`);
  }
  assert(new Set(rules).size === rules.length, "duplicate scoped rules");
  const bg = await one.$eval("button", (b) => getComputedStyle(b).backgroundColor);
  assert(bg === "rgb(101, 84, 217)", `styled CSS not applied, background=${bg}`);
  ok("shared scoped rules are unique and the themed button accent is applied");

  for (let i = 0; i < 3; i++) {
    await one.click("button");
    await one.waitForFunction(
      (n) => document.querySelector("pysx-slot").textContent === String(n),
      i + 1,
    );
  }
  assert((await read(one)).includes("Count: 3"), `tab 1 = ${await read(one)}`);
  ok("3 clicks -> Count: 3");

  assert(documentRequests === 1, `page reloaded: ${documentRequests} document requests`);
  ok("no page reload (1 document request total)");

  await two.waitForTimeout(300);
  assert((await read(two)).includes("Count: 0"), `tab 2 moved: ${await read(two)}`);
  ok("tab 2 still at Count: 0 (session isolation in the browser)");

  console.log("BROWSER ACCEPTANCE PASSED");
} finally {
  await browser.close();
}
