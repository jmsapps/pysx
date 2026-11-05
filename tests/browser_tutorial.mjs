import assert from "node:assert/strict";
import playwright from "playwright";
import { tmpdir } from "node:os";

const controls = `http://127.0.0.1:${process.argv[2]}`;
const control = async action => {
  const response = await fetch(controls + action);
  assert.equal(response.status, 200, action);
  return response.json();
};
const {base} = await control("/reset");
const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
const layoutIssues = root => {
  const issues = [];
  const document = root.ownerDocument;
  const view = document.defaultView;
  if (document.documentElement.scrollWidth > view.innerWidth + 1) issues.push("page overflows horizontally");
  const controls = [...root.querySelectorAll("input, button, textarea, select")]
    .map(node => ({node, rect: node.getBoundingClientRect()}))
    .filter(({rect}) => rect.width > 0 && rect.height > 0);
  for (const [index, {node, rect}] of controls.entries()) {
    const name = node.id || node.textContent.trim() || node.tagName;
    if (rect.left < -1 || rect.right > view.innerWidth + 1) issues.push(`${name} leaves the viewport`);
    if (node.type === "checkbox" && !node.closest("label")) issues.push(`${name} has no grouped label`);
    for (const other of controls.slice(index + 1)) {
      const horizontal = Math.min(rect.right, other.rect.right) - Math.max(rect.left, other.rect.left);
      const vertical = Math.min(rect.bottom, other.rect.bottom) - Math.max(rect.top, other.rect.top);
      if ((horizontal > 1 && vertical > -8) || (vertical > 1 && horizontal > -8)) {
        issues.push(`${name} crowds ${other.node.id || other.node.textContent.trim()}`);
      }
    }
  }
  for (const list of root.querySelectorAll("#keyed-rows, #tasks")) {
    if (view.getComputedStyle(list).listStyleType !== "none") issues.push(`${list.id} has stray bullets`);
  }
  const reset = root.querySelector(".demo-reset");
  const demoBottom = Math.max(0, ...controls.filter(({node}) => !reset?.contains(node)).map(({rect}) => rect.bottom));
  if (reset && reset.getBoundingClientRect().top - demoBottom < 16) {
    issues.push("reset actions crowd the demonstration");
  }
  return issues;
};
try {
  const context = await browser.newContext();
  await context.addInitScript(() => {
    const Original = window.WebSocket;
    window.courseConnectionEvents = [];
    window.addEventListener("pysx:connection", event => window.courseConnectionEvents.push(event.detail));
    window.WebSocket = class extends Original {
      constructor(...args) { super(...args); window.courseSocket = this; }
    };
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const text = (id, value, target = page) => target.waitForFunction(([id, value]) =>
    document.getElementById(id)?.textContent === value, [id, value]).catch(async error => {
      const state = await target.evaluate(id => ({id, text: document.getElementById(id)?.textContent,
        connection: document.getElementById("pysx-root")?.dataset.pysxConnection}), id);
      throw new Error(`expected ${id} = ${value}; observed ${JSON.stringify(state)}`, {cause: error});
    });
  const connected = (target = page) => target.waitForFunction(() =>
    document.getElementById("pysx-root")?.dataset.pysxConnection === "connected", null, {timeout: 30000})
    .catch(async error => {
      const state = await target.evaluate(() => ({
        connection: document.getElementById("pysx-root")?.dataset.pysxConnection,
        events: window.courseConnectionEvents,
      }));
      throw new Error(`${error.message}; ${JSON.stringify(state)}`, {cause: error});
    });
  const reconnect = async () => {
    await page.evaluate(() => { window.previousSocket = window.courseSocket; window.courseSocket.close(); });
    await page.waitForFunction(() => window.courseSocket !== window.previousSocket &&
      document.getElementById("pysx-root")?.dataset.pysxConnection === "connected", null, {timeout: 30000});
  };
  const fresh = async (target, slug) => {
    const initial = {
      "templates-and-elements": ["card-preview", "A readable interface"],
      "live-signals": ["live-count", "0"],
      "derived-values": ["total", "12"],
      "browser-events": ["event-report", "No key yet"],
      "forms-and-bindings": ["form-result", "Not submitted"],
      "structured-state": ["score-first", "1"],
      "conditional-views": ["parent-count", "0"],
      "lists-and-loops": ["group-selection", "None"],
      "components-and-children": ["component-title", null],
      "effects-and-lifetimes": ["effect-log", "run 0"],
      "keyboard-and-focus": ["chooser-selection", "None"],
      "async-resources": ["resource-setups", "1"],
      "server-rendering": ["resource-setups", "1"],
      "errors-and-retry": ["sibling-count", "0"],
      "session-isolation": ["private-echo", ""],
      "reconnection": ["recovery-count", "0"],
      "authentication": ["auth-count", "0"],
      "hosting-and-deployment": ["host-result", "Local host: render capacity 25"],
    }[slug];
    if (initial && initial[1] !== null) await text(initial[0], initial[1], target);
    const inputs = {"keyed-identity": "draft-alpha", "styles-and-themes": "style-draft",
      "components-and-children": "component-title", "capstone-workspace": "new-task"};
    if (inputs[slug]) {
      const expected = slug === "components-and-children" ? "Shared heading" : "";
      await target.waitForFunction(([id, expected]) => document.getElementById(id)?.value === expected,
        [inputs[slug], expected]);
    }
    if (["routes-and-parameters", "links-and-history"].includes(slug)) await text("route-count", "0", target);
    if (slug === "capstone-workspace") await target.waitForFunction(() =>
      document.querySelectorAll("[data-task]").length === 2 && document.querySelector("#task-note-1")?.value === "");
  };
  const slugs = [
    "getting-started", "templates-and-elements", "live-signals", "derived-values", "browser-events",
    "forms-and-bindings", "structured-state", "conditional-views", "lists-and-loops", "keyed-identity",
    "components-and-children", "effects-and-lifetimes", "styles-and-themes", "routes-and-parameters",
    "links-and-history", "keyboard-and-focus", "async-resources", "errors-and-retry", "server-rendering",
    "session-isolation", "reconnection", "browser-recovery", "durable-state", "authentication",
    "development-workflow", "hosting-and-deployment", "capstone-workspace", "capstone-resilience",
  ];
  const localPlaygrounds = new Map([
    ["getting-started", "counter"], ["browser-recovery", "recovery"],
    ["durable-state", "durable"], ["development-workflow", "development"],
    ["capstone-resilience", "capstone"],
  ]);
  for (const [index, slug] of slugs.entries()) {
    if (slug === "capstone-resilience") await page.request.get(base + "/demo-login/writer");
    const response = await page.goto(base + "/learn/" + slug);
    assert.equal(response.status(), 200, slug);
    await connected();
    assert.equal(await page.locator(`[data-lesson="${slug}"]`).count(), 1);
    assert.equal(await page.locator('.contents a[aria-current="page"]').count(), 1);
    assert.equal(await page.locator('.contents a[aria-current="page"]').getAttribute("href"), "/learn/" + slug);
    assert.equal(await page.locator("[data-previous]").count(), 2);
    assert.equal(await page.locator("[data-next]").count(), 2);
    const prior = index ? "/learn/" + slugs[index - 1] : "/";
    const next = index < 27 ? "/learn/" + slugs[index + 1] : "/review";
    for (const position of ["top", "bottom"]) {
      assert.equal(await page.locator(`[data-previous="${position}"]`).getAttribute("href"), prior);
      assert.equal(await page.locator(`[data-next="${position}"]`).getAttribute("href"), next);
    }
    assert((await page.locator("article > pre code").textContent()).includes("from pysx"));
    assert(await page.locator("article pre code [data-syntax]").count() > 0);
    await page.locator("#lesson-main").focus();
    assert.equal(await page.locator("#lesson-main").evaluate(node => getComputedStyle(node).outlineStyle), "none");
    const playground = localPlaygrounds.get(slug);
    assert(await page.locator(".demo").evaluate((node, playground) => {
      const exercise = node.querySelector("#lesson-exercise");
      const expected = node.querySelector("#lesson-expected");
      const reset = node.querySelector("#reset-demo");
      return Boolean(exercise && expected && (playground ? !reset : reset) &&
        (exercise.compareDocumentPosition(expected) & Node.DOCUMENT_POSITION_FOLLOWING) &&
        (playground || (expected.compareDocumentPosition(reset) & Node.DOCUMENT_POSITION_FOLLOWING)));
    }, playground));
    assert.equal(await page.getByRole("heading", {name: "Your turn", exact: true}).count(), 0);
    for (const width of [1280, 375]) {
      await page.setViewportSize({width, height: 812});
      assert.deepEqual(await page.locator('.demo').evaluate(layoutIssues), [], `${slug}, ${width}px`);
    }
    await page.setViewportSize({width: 1280, height: 812});
    if (index === 0) {
      const palette = () => page.evaluate(() => ({
        paper: getComputedStyle(document.body).backgroundColor,
        accent: getComputedStyle(document.querySelector('[data-previous="top"]')).color,
        detail: getComputedStyle(document.querySelector('.eyebrow')).color,
      }));
      assert.deepEqual(await palette(), {paper: "rgb(247, 249, 254)", accent: "rgb(21, 94, 217)", detail: "rgb(104, 53, 201)"});
      await page.locator('#course-theme').click();
      await text('course-theme', 'Theme: dark');
      assert.deepEqual(await palette(), {paper: "rgb(9, 22, 45)", accent: "rgb(84, 205, 255)", detail: "rgb(189, 154, 255)"});
      await page.screenshot({path: `${tmpdir()}/pysx-course-dark-${process.env.PYSX_BROWSER_ENGINE}.png`, fullPage: true});
      await page.locator('#course-theme').click();
      await text('course-theme', 'Theme: light');
    }
    if (slug === "templates-and-elements") {
      await page.locator('#card-emphasis').focus();
      await page.keyboard.press("Shift+Tab");
      assert(await page.locator('#card-text').evaluate(node =>
        node === document.activeElement && node.matches(":focus-visible") &&
        getComputedStyle(node).outlineStyle !== "none" && parseFloat(getComputedStyle(node).outlineWidth) > 0));
    }
    if (playground) {
      assert.deepEqual(await page.locator(".demo h2, .demo h3").allTextContents(),
        ["Try it locally", "What you'll do", "What you'll see"]);
      const commands = await page.locator(".demo pre").textContent();
      assert(commands.includes("pip install"));
      assert(commands.includes(`pysx-playground ${playground}`));
      assert(commands.includes(`pysx --app playgrounds.${playground}`));
      assert(await page.locator("#lesson-exercise li").count() >= 3);
      assert.equal(await page.locator(".demo input, .demo button, .demo textarea, .demo iframe").count(), 0);
      await page.reload(); await connected();
      assert.equal(await page.getByRole("heading", {name: "Try it locally", exact: true}).count(), 1);
      console.log(`  ok  ${index + 1}: ${slug} local playground instructions, reload and named navigation`);
      continue;
    }
    const isolated = await context.newPage();
    await isolated.goto(base + "/learn/" + slug); await connected(isolated);
    if (slug === "templates-and-elements") {
      await page.locator("#card-text").fill("<em>Hello</em>"); await text("card-preview", "<em>Hello</em>");
      assert.equal(await page.locator("#card-preview em").count(), 0);
      await page.locator("#card-emphasis").click();
      await page.waitForFunction(() => document.querySelector("#card-preview").style.fontWeight === "800");
    } else if (slug === "live-signals") {
      await page.locator("#increment").click(); await text("live-count", "1");
      await page.locator("#increment").click(); await text("live-count", "2");
      assert.equal(await page.locator("#snapshot-count").textContent(), "0");
    } else if (slug === "derived-values") {
      await text("threshold", "False"); await text("within-limit", "True");
      for (let i = 0; i < 3; i++) await page.locator("#quantity-increase").click();
      await text("total", "48"); await text("threshold", "True");
      await text("within-limit", "False");
    } else if (slug === "browser-events") {
      await page.locator("#event-input").press("Shift+A");
      await page.waitForFunction(() => document.querySelector("#event-report").textContent.includes("Shift: True"));
    } else if (slug === "forms-and-bindings") {
      await page.locator("#form-submit").click();
      assert(await page.locator("#form-name").evaluate(node => node.validity.valueMissing));
      await page.locator("#form-name").fill("Ada"); await page.locator("#form-terms").check();
      await page.locator("#form-submit").click(); await text("form-result", "Accepted Ada");
      await page.locator("#form-correct").click(); await text("form-echo", "Server correction");
      assert.equal(await page.locator("#form-name").inputValue(), "Server correction");
    } else if (slug === "structured-state") {
      await page.locator("#score-increment").click(); await text("score-first", "2");
      await page.locator("#score-copy").click(); await text("score-copy-value", "99");
      assert.equal(await page.locator("#score-first").textContent(), "2");
      assert.equal(await page.locator("#score-second").textContent(), "2");
    } else if (slug === "conditional-views") {
      await page.locator("#parent-increment").click(); await page.locator("#child-increment").click();
      await text("child-count", "1"); await page.locator("#branch-toggle").click();
      await page.waitForSelector("#child-absent"); await text("branch-cleanups", "1");
      await page.locator("#branch-toggle").click(); await text("child-count", "0"); await text("parent-count", "1");
    } else if (slug === "lists-and-loops") {
      await page.locator("#group-add").click(); await page.locator("#group-reverse").click();
      await page.waitForFunction(() => document.querySelector("[data-group]").dataset.group === "Group 3");
      await page.locator('[data-choice="Read-first"]').click(); await text("group-selection", "Read / first");
    } else if (slug === "keyed-identity") {
      await page.locator("#draft-alpha").fill("Keep my draft");
      const original = await page.locator("#draft-alpha").elementHandle();
      await page.locator('[data-increment="alpha"]').click();
      await page.locator('[data-move="alpha"]').click();
      await page.waitForFunction(() => document.activeElement?.id === "draft-alpha");
      assert(await original.evaluate(node => node === document.querySelector("#draft-alpha")));
      assert.equal(await page.locator("#draft-alpha").inputValue(), "Keep my draft");
      await page.locator("#row-remove").click(); await page.waitForFunction(() => !document.querySelector("#draft-alpha"));
      await page.locator("#row-restore").click(); await page.waitForSelector("#draft-alpha");
      assert.equal(await page.locator("#draft-alpha").inputValue(), "");
    } else if (slug === "components-and-children") {
      await page.locator("#component-title").fill("New shared heading");
      await page.locator('[data-card-increment="1"]').click();
      await page.waitForFunction(() => document.querySelector('[data-card-count="1"]').textContent === "1");
      assert.equal(await page.locator('[data-card-count="2"]').textContent(), "0");
      assert.deepEqual(await page.locator("[data-instance] h3").allTextContents(), ["New shared heading", "New shared heading"]);
    } else if (slug === "effects-and-lifetimes") {
      await page.locator("#effect-increment").click(); await text("effect-log", "run 0 | cleanup 0 | run 1");
      await page.locator("#effect-toggle").click();
      await page.waitForFunction(() => !document.querySelector("#observer-active"));
      const before = await page.locator("#effect-log").textContent();
      await page.locator("#effect-increment").click(); await text("effect-source", "2");
      assert.equal(await page.locator("#effect-log").textContent(), before);
    } else if (slug === "styles-and-themes") {
      const original = await page.locator("#style-draft").elementHandle();
      await page.locator("#style-draft").fill("Retained input");
      await page.locator("#preview-spacing").click();
      await page.waitForFunction(() => getComputedStyle(document.querySelector("#style-preview")).paddingLeft === "8px");
      await page.locator("#preview-theme").click(); await text("preview-theme-name", "dark");
      assert(await original.evaluate(node => node === document.querySelector("#style-draft")));
    } else if (["routes-and-parameters", "links-and-history"].includes(slug)) {
      await page.locator("#route-one").click(); await text("route-location", `/learn/${slug}/projects/1`);
      await text("route-project", "1");
      await page.locator("#route-increment").click(); await text("route-count", "1");
      await page.locator("#route-two").click(); await text("route-project", "2");
      await text("route-count", "1");
      await page.goBack(); await text("route-project", "1");
      await page.locator("#route-query").click(); await text("route-location", `/learn/${slug}/projects/1?tab=details`);
      await page.goBack(); await text("route-location", `/learn/${slug}`);
      await page.goForward(); await text("route-location", `/learn/${slug}/projects/1?tab=details`);
      const url = page.url(); await page.locator("#route-cancel").click(); await text("route-notice", "Navigation cancelled");
      assert.equal(page.url(), url);
    } else if (slug === "keyboard-and-focus") {
      await page.locator("#chooser").press("ArrowDown"); await page.locator("#chooser").press("ArrowDown");
      await page.locator("#chooser").press("Enter"); await text("chooser-selection", "Ship");
      assert.equal(await page.locator("#chooser").getAttribute("aria-expanded"), "false");
      await page.locator("#focus-chooser").click();
      await page.waitForFunction(() => document.activeElement?.id === "chooser");
      await page.locator("#lesson-tree-root").focus();
      await page.keyboard.press("ArrowRight");
      await page.waitForSelector("#lesson-tree-read");
      await page.keyboard.press("ArrowRight");
      await page.waitForFunction(() => document.activeElement?.id === "lesson-tree-read");
      await page.keyboard.press("Enter"); await text("tree-selection", "Read");
      await page.waitForFunction(() => document.querySelector('#lesson-tree-read')?.getAttribute('aria-selected') === 'true');
      assert.equal(await page.locator('#lesson-tree [tabindex="0"]').count(), 1);
    } else if (["async-resources", "server-rendering"].includes(slug)) {
      await text("resource-status", "success"); await text("resource-setups", "1"); await text("resource-mounts", "1");
      await page.locator("#resource-draft").fill("Unblocked draft");
      await page.locator("#resource-reload").click(); await text("resource-status", "pending");
      await page.locator("#resource-cancel").click(); await text("resource-status", "cancelled");
      await page.waitForTimeout(450); assert.equal(await page.locator("#resource-status").textContent(), "cancelled");
      await page.locator("#resource-fail").click(); await text("resource-status", "error");
      await page.locator("#resource-reload").click(); await text("resource-status", "success");
      assert.equal(await page.locator("#resource-draft").inputValue(), "Unblocked draft");
      await text("resource-setups", "1");
    } else if (slug === "errors-and-retry") {
      await page.locator("#sibling-increment").click(); await text("sibling-count", "1");
      await page.locator("#boundary-fail").click(); await page.waitForSelector("#boundary-fallback");
      await page.locator("#boundary-retry").click(); await page.waitForSelector("#boundary-content");
      await text("sibling-count", "1");
    } else if (slug === "session-isolation") {
      await page.locator("#private-draft").fill("Only here"); await text("private-echo", "Only here");
      const other = await context.newPage(); await other.goto(page.url()); await connected(other);
      assert.equal(await other.locator("#private-draft").inputValue(), ""); await other.close();
    } else if (slug === "reconnection") {
      await page.locator("#recovery-draft").fill("Delivered draft"); await text("recovery-echo", "Delivered draft");
      await page.locator("#recovery-increment").click(); await text("recovery-count", "1");
      if (slug === "reconnection") {
        const setup = await page.locator("#recovery-setup").textContent();
        const ticks = Number(await page.locator("#recovery-ticks").textContent());
        await text("recovery-connection", "Connected");
        // Invalid durations must not interrupt a healthy connection.
        await page.evaluate(() => {
          window.previousSocket = window.courseSocket;
          for (const durationMs of [-1, 0, 10001, Infinity, "5000"]) {
            window.dispatchEvent(new CustomEvent("pysx:disconnect", {detail: {durationMs}}));
          }
        });
        assert(await page.evaluate(() => window.courseSocket === window.previousSocket &&
          window.courseSocket.readyState === WebSocket.OPEN));
        const started = Date.now();
        await page.locator("#recovery-disconnect").click();
        await text("recovery-connection", "Disconnected. Reconnecting…");
        assert(await page.locator("#recovery-disconnect").isDisabled());
        // A manual retry cannot bypass the pause; clicks are rejected, never replayed.
        await page.evaluate(() => {
          window.dispatchEvent(new Event("pysx:reconnect"));
          document.getElementById("recovery-increment").click();
          window.dispatchEvent(new CustomEvent("pysx:disconnect", {detail: {durationMs: 1}}));
        });
        await connected();
        assert(Date.now() - started >= 4900, "real socket stayed disconnected for five seconds");
        assert(await page.evaluate(() => window.courseSocket !== window.previousSocket));
        await text("recovery-connection", "Connected");
        assert(await page.locator("#recovery-disconnect").isEnabled());
        assert.equal(await page.locator("#recovery-setup").textContent(), setup, "same server owner");
        assert(Number(await page.locator("#recovery-ticks").textContent()) >= ticks + 8,
          "server clock continued during disconnect");
      }
      await text("recovery-count", "1"); assert.equal(await page.locator("#recovery-draft").inputValue(), "Delivered draft");
    } else if (slug === "authentication") {
      await page.locator("#auth-reader").click(); await connected(); await text("auth-principal", "reader");
      await page.locator("#auth-write").click(); await text("auth-result", "Denied by current host policy");
      await page.locator("#auth-writer").click(); await connected(); await text("auth-principal", "writer");
      await page.locator("#auth-write").click(); await text("auth-count", "1");
      await reconnect(); await text("auth-count", "1");
      // Change the actual HttpOnly login through the application's own endpoint,
      // retaining this document/capsule rather than navigating to a fresh page.
      const changedLogin = await context.request.get(base + "/demo-login/reader", {maxRedirects: 0});
      assert.equal(changedLogin.status(), 303);
      await reconnect(); await text("auth-principal", "reader"); await text("auth-count", "0");
      await page.locator("#auth-logout").click(); await connected(); await text("auth-principal", "learner"); await text("auth-count", "0");
    } else if (slug === "hosting-and-deployment") {
      await page.locator("#host-invalid").click();
      await page.waitForFunction(() => document.querySelector("#host-result").textContent.startsWith("Rejected:"));
      await page.locator("#host-complete").click();
      await text("host-result", "Accepted complete production configuration: True");
    } else if (slug === "capstone-workspace") {
      await page.locator("#new-task").fill("Ship course"); await page.locator("#task-add").click();
      await page.waitForSelector('[data-task="3"]'); await page.locator("#task-note-1").fill("Keep this note");
      await page.locator("#task-reverse").click();
      await page.waitForFunction(() => document.querySelector("[data-task]").dataset.task === "3");
      assert.equal(await page.locator("#task-note-1").inputValue(), "Keep this note");
      await page.locator('[data-task-toggle="1"]').click(); await page.locator("#task-active").check();
      await page.waitForFunction(() => !document.querySelector('[data-task="1"]'));
    }
    await fresh(isolated, slug); await isolated.close();
    await connected();
    const generation = await page.locator("[data-lesson]").getAttribute("data-demo-generation");
    await page.locator("#reset-demo").click();
    await page.waitForFunction(generation => document.querySelector("[data-lesson]")?.getAttribute("data-demo-generation") !== generation, generation);
    await fresh(page, slug);
    console.log(`  ok  ${index + 1}: ${slug} subject interaction, correction/reset and named navigation`);
    if (index === 0) {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({path: `${tmpdir()}/pysx-course-wide-${process.env.PYSX_BROWSER_ENGINE}.png`, fullPage: true});
    }
  }
  await page.goto(base + "/"); await connected();
  await page.setViewportSize({width: 1280, height: 812});
  await page.goto(base + "/learn/durable-state"); await connected();
  const headerTop = await page.locator(".course-header").evaluate(node => node.getBoundingClientRect().top);
  const sidebar = page.locator(".course-layout > aside");
  const main = page.locator("#lesson-main");
  await sidebar.hover();
  await page.mouse.wheel(0, 10000);
  await page.waitForFunction(() => {
    const list = document.querySelector(".course-layout > aside");
    return list.scrollTop > 0 && list.scrollTop + list.clientHeight >= list.scrollHeight - 2;
  });
  assert.equal(await main.evaluate(node => node.scrollTop), 0);
  assert.equal(await page.evaluate(() => scrollY), 0);
  assert.equal(await page.locator(".course-header").evaluate(node => node.getBoundingClientRect().top), headerTop);
  const sidebarPosition = await sidebar.evaluate(node => node.scrollTop);
  await main.hover({position: {x: 10, y: 10}});
  // Firefox limits a single wheel gesture; exercise several ordinary gestures.
  for (let gesture = 0; gesture < 12; gesture += 1) {
    await page.mouse.wheel(0, 1000);
    if (await main.evaluate(node => node.scrollTop + node.clientHeight >= node.scrollHeight - 2)) break;
    await main.evaluate(() => new Promise(requestAnimationFrame));
  }
  await page.waitForFunction(() => {
    const main = document.getElementById("lesson-main");
    return main.scrollTop > 0 && main.scrollTop + main.clientHeight >= main.scrollHeight - 2;
  });
  const sidebarAfter = await sidebar.evaluate(node => node.scrollTop);
  assert.ok(Math.abs(sidebarAfter - sidebarPosition) <= 1,
    `sidebar scrolled with the lesson pane: ${sidebarAfter} vs ${sidebarPosition}`);
  assert.equal(await page.evaluate(() => scrollY), 0);
  assert.equal(await page.locator(".course-header").evaluate(node => node.getBoundingClientRect().top), headerTop);
  const readingPosition = await main.evaluate(node => node.scrollTop);
  await page.locator('[data-next="bottom"]').click();
  await page.waitForURL("**/learn/authentication");
  await page.waitForFunction(() => document.querySelector('[data-lesson="authentication"]') &&
    document.getElementById("lesson-main").scrollTop === 0);
  await page.goBack();
  await page.waitForFunction(position => document.querySelector('[data-lesson="durable-state"]') &&
    Math.abs(document.getElementById("lesson-main").scrollTop - position) < 2, readingPosition);
  await page.goto(base + "/"); await connected();
  await page.locator(".lesson-nav a").click(); await page.waitForURL("**/learn/getting-started");
  await page.locator('[data-next="bottom"]').click(); await page.waitForURL("**/learn/templates-and-elements");
  await page.waitForFunction(() => document.querySelector('.contents a[aria-current="page"]')?.getAttribute('href') === '/learn/templates-and-elements');
  await page.locator('[data-previous="top"]').click(); await page.waitForURL("**/learn/getting-started");
  await page.setViewportSize({width: 375, height: 812});
  assert.equal(await main.evaluate(node => getComputedStyle(node).overflowY), "visible");
  assert.equal(await sidebar.evaluate(node => getComputedStyle(node).overflowY), "visible");
  await page.locator('[data-next="bottom"]').scrollIntoViewIfNeeded();
  assert(await page.evaluate(() => scrollY > 0));
  await page.evaluate(() => window.scrollTo(0, 0));
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path: `${tmpdir()}/pysx-course-narrow-${process.env.PYSX_BROWSER_ENGINE}.png`, fullPage: true});
  await page.locator("summary").focus();
  assert(await page.locator("summary").evaluate(node => document.activeElement === node));
  await page.goto(base + "/review"); await connected();
  await page.locator("#new-task").fill("Finish the review");
  await page.locator("#task-add").click(); await page.locator('[data-task="3"]').waitFor();
  await page.locator("#task-note-3").fill("Retain the final practice note");
  await page.locator("#task-reverse").click();
  await page.waitForFunction(() => document.querySelector("[data-task]")?.dataset.task === "3");
  assert.equal(await page.locator("#task-note-3").inputValue(), "Retain the final practice note");
  await page.locator('[data-task-toggle="3"]').click();
  await page.locator("#task-active").check();
  await page.waitForFunction(() => !document.querySelector('[data-task="3"]'));
  await page.locator("#task-active").uncheck(); await page.locator('[data-task="3"]').waitFor();
  assert.equal(await page.locator("#task-note-3").inputValue(), "Retain the final practice note");
  await page.locator('[data-task-remove="3"]').click();
  await page.waitForFunction(() => !document.querySelector('[data-task="3"]'));
  await page.locator("#task-reset").click();
  await page.waitForFunction(() => document.querySelectorAll("[data-task]").length === 2 &&
    document.querySelector("[data-task]")?.dataset.task === "1" &&
    document.getElementById("task-note-1")?.value === "");
  console.log("  ok  final review practice retains task edits, notes, order and filters until explicit removal/reset");
  assert.equal(await page.locator('[aria-disabled="true"]').count(), 0);
  const returnToOverview = page.getByRole("link", {name: "Return to course overview", exact: true});
  assert.equal(await returnToOverview.count(), 1);
  assert.equal(await returnToOverview.getAttribute("href"), "/");
  assert(await returnToOverview.evaluate(node => node.parentElement.matches("nav.lesson-nav")));
  await returnToOverview.click();
  await page.waitForURL(base + "/");
  await connected();
  const development = await control("/playground/start/development");
  const editorPage = await context.newPage();
  editorPage.on("pageerror", error => errors.push(error.message));
  await editorPage.goto(development.base); await connected(editorPage);
  await editorPage.locator("#increment").click(); await text("count", "1", editorPage);
  await editorPage.locator("#draft").fill("Keep this draft");
  await text("draft-echo", "Keep this draft", editorPage);
  const edit = async action => {
    const result = await control("/playground/edit/" + action);
    if (action !== "broken") {
      await editorPage.waitForFunction(action => document.querySelector("h1")?.textContent ===
        `Edited development playground: ${action}`, action);
      await connected(editorPage);
    }
    return result.diagnostics;
  };
  assert.deepEqual(await edit("heading"), []);
  await text("count", "1", editorPage);
  assert.equal(await editorPage.locator("#draft").inputValue(), "Keep this draft");
  assert(/eager|frozen|snapshot/i.test(JSON.stringify(await edit("eager"))));
  const frozenCount = await editorPage.locator("#count").textContent();
  await editorPage.locator("#increment").click(); await text("doubled", "4", editorPage);
  assert.equal(await editorPage.locator("#count").textContent(), frozenCount);
  assert(JSON.stringify(await edit("attribute")).includes("tittle"));
  await text("count", "2", editorPage);
  await edit("broken");
  await editorPage.waitForFunction(async () => {
    const state = await (await fetch("/__pysx_dev__/status")).json();
    return Boolean(state.error);
  });
  await editorPage.locator("#increment").click(); await text("count", "3", editorPage);
  assert.deepEqual(await edit("repair"), []);
  await text("count", "3", editorPage);
  assert.equal(await editorPage.locator("#draft").inputValue(), "Keep this draft");
  await editorPage.close(); await control("/playground/stop");
  console.log("  ok  scaffolded development: diagnostics, eager/live correction, retained edits and broken-candidate recovery");

  const capstone = await control("/playground/start/capstone");
  const workspace = await context.newPage();
  workspace.on("pageerror", error => errors.push(error.message));
  await workspace.goto(capstone.base); await connected(workspace);
  await text("validation-status", "success", workspace);
  await workspace.locator("#new-task").fill("Persist this task");
  await workspace.locator("#task-add").click(); await workspace.locator('[data-task="3"]').waitFor();
  await workspace.locator("#task-note-1").fill("First row note");
  await workspace.locator("#task-note-3").fill("Saved new row note");
  await workspace.locator("#task-reverse").click();
  await workspace.waitForFunction(() => document.querySelector("[data-task]")?.dataset.task === "3");
  await workspace.locator('[data-task-toggle="1"]').click();
  await workspace.locator("#task-active").check();
  await workspace.waitForFunction(() => !document.querySelector('[data-task="1"]'));
  await workspace.locator("#task-active").uncheck();
  await workspace.locator('[data-task="1"]').waitFor();
  await workspace.locator("#validation-fail").click(); await text("validation-status", "error", workspace);
  await workspace.locator("#validation-retry").click(); await text("validation-status", "success", workspace);
  const setup = await workspace.locator("#setup").textContent();
  await control("/playground/restart");
  await workspace.waitForFunction(setup => document.getElementById("setup")?.textContent !== setup &&
    document.getElementById("pysx-root")?.dataset.pysxConnection === "connected", setup);
  assert.equal(await workspace.locator("#task-note-1").inputValue(), "First row note");
  assert.equal(await workspace.locator("#task-note-3").inputValue(), "Saved new row note");
  assert.equal(await workspace.locator('[data-task="3"] h3').textContent(), "Persist this task");
  assert.equal(await workspace.locator("[data-task]").first().getAttribute("data-task"), "3");
  await workspace.locator("#task-active").check();
  await workspace.waitForFunction(() => !document.querySelector('[data-task="1"]'));
  await workspace.locator("#task-active").uncheck();
  await workspace.locator("#new-task").fill("Added after restart");
  await workspace.locator("#task-add").click();
  await workspace.locator('[data-task="4"]').waitFor();
  await workspace.locator("#validation-fail").click(); await text("validation-status", "error", workspace);
  await workspace.locator("#validation-retry").click(); await text("validation-status", "success", workspace);
  await text("validation-message", "Validated 4 tasks", workspace);
  const freshWorkspace = await context.newPage();
  await freshWorkspace.goto(capstone.base); await connected(freshWorkspace);
  assert.equal(await freshWorkspace.locator("[data-task]").count(), 2);
  assert.equal(await freshWorkspace.locator("#task-note-1").inputValue(), "");
  await freshWorkspace.close();
  await workspace.locator("#task-reset").click();
  await workspace.waitForFunction(() => document.querySelectorAll("[data-task]").length === 2 &&
    document.getElementById("task-note-1")?.value === "");
  await workspace.close(); await control("/playground/stop");
  console.log("  ok  scaffolded capstone: keyed edits, filters, real restart, task reconstruction, rebuilt resources and isolation");
  assert.deepEqual(errors, []);
  console.log("  ok  overview/review boundaries, actual adjacent links, responsive layout and keyboard access");
  console.log("TUTORIAL WEBSITE BROWSER PASSED");
} finally { await browser.close(); }
