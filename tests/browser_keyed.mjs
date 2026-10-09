import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  await page.addInitScript(() => {
    const NativeSocket = window.WebSocket;
    window.metrics = {listen: 0, rowHtml: 0};
    window.WebSocket = class extends NativeSocket {
      constructor(...args) {
        super(...args);
        window.socketProbe = this;
        this.addEventListener("message", event => {
          const message = JSON.parse(event.data);
          if (message.t === "dom" && message.op === "listen") window.metrics.listen++;
          if (message.t === "patch") for (const op of message.ops) if (op.op === "list") window.metrics.rowHtml += Object.keys(op.html).length;
        });
      }
    };
  });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${process.argv[2]}`);
  await page.waitForSelector('[data-control="a"]');
  await page.waitForFunction(() => window.metrics.listen === 2);
  await page.evaluate(() => {
    window.saved = {row: document.querySelector('[data-row="a"]'), field: document.querySelector('[data-control="a"]'), footer: document.querySelector('[data-footer="a"]'), child: document.querySelector('[data-child="a:one"]')};
    window.token = window.saved.field.dataset.pysxRef;
  });
  await page.locator('[data-control="a"]').fill("typed draft");
  await page.locator('[data-control="a"]').evaluate(node => node.setSelectionRange(2, 5, "backward"));
  await page.locator('[data-scroll="a"]').evaluate(node => { node.scrollTop = 70; });
  await page.evaluate(() => document.querySelector('#reverse').click());
  await page.waitForFunction(() => document.querySelector('[data-row]')?.dataset.row === 'b');
  assert.equal(await page.evaluate(() => window.saved.field === document.querySelector('[data-control="a"]') && document.activeElement === window.saved.field && window.saved.field.dataset.pysxRef === window.token), true);
  assert.deepEqual(await page.locator('[data-control="a"]').evaluate(node => [node.value, node.selectionStart, node.selectionEnd, node.selectionDirection]), ["typed draft", 2, 5, "backward"]);
  assert.equal(await page.locator('[data-scroll="a"]').evaluate(node => node.scrollTop), 70);
  console.log("  ok  refs/focus/caret/selection/scroll survive multiple-root reorder");
  await page.evaluate(() => document.querySelector('#edit').click());
  await page.waitForFunction(() => document.querySelector('[data-row="a"]')?.title === 'Current');
  assert.equal(await page.locator('[data-row="a"]').getAttribute('aria-label'), "Current");
  assert.equal(await page.locator('[data-row="a"]').getAttribute('role'), "group");
  assert.equal(await page.evaluate(() => window.saved.row === document.querySelector('[data-row="a"]') && window.saved.field === document.querySelector('[data-control="a"]') && window.saved.footer === document.querySelector('[data-footer="a"]')), true);
  assert.deepEqual(await page.locator('[data-control="a"]').evaluate(node => [node.value, node.selectionStart, node.selectionEnd]), ["typed draft", 2, 5]);
  assert.equal(await page.locator('[data-row="b"]').getAttribute('data-build'), "1");
  await page.locator('[data-pick="a"]').click();
  await page.waitForFunction(() => document.querySelector('#selected')?.textContent === 'Current');
  await page.locator('[data-count="a"]').click();
  await page.waitForFunction(() => document.querySelector('[data-count="a"]')?.textContent === '1');
  assert.equal(await page.locator('[data-row="a"]').getAttribute('data-build'), "2");
  console.log("  ok  same-key text/attrs/current closures and local signals patch retained nodes");
  await page.locator('#nested').click();
  await page.waitForFunction(() => document.querySelectorAll('[data-nested="a"] > span').length === 3);
  assert.equal(await page.evaluate(() => window.saved.child === document.querySelector('[data-child="a:one"]')), true);
  assert.equal(await page.locator('[data-row="a"]').getAttribute('data-build'), "2");
  console.log("  ok  nested lists update independently and retain moved children");
  await page.evaluate(() => {
    const text = document.querySelector('[data-selection="a"]').firstChild;
    window.getSelection().setBaseAndExtent(text, 2, text, 6);
    window.metrics.rowHtml = 0;
  });
  for (let cycle = 0; cycle < 250; cycle++) {
    await page.evaluate(() => document.querySelector('#reverse').click());
    await page.waitForFunction(key => document.querySelector('[data-row]')?.dataset.row === key, cycle % 2 === 0 ? "a" : "b");
  }
  assert.equal(await page.evaluate(() => window.getSelection().toString()), "lect");
  assert.equal(await page.evaluate(() => window.metrics.rowHtml), 0);
  assert.equal(await page.evaluate(() => window.saved.row === document.querySelector('[data-row="a"]') && window.saved.child === document.querySelector('[data-child="a:one"]')), true);
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("custom-keyed")));
  await page.waitForFunction(() => document.querySelector('#listened')?.textContent === '2');
  console.log("  ok  250 reorders retain selection/nodes and mounted listeners without row HTML");
  await page.locator('#remount').click();
  await page.waitForSelector('textarea[data-control="a"]');
  assert.equal(await page.evaluate(() => !window.saved.field.isConnected && window.saved.row === document.querySelector('[data-row="a"]') && document.querySelector('[data-control="a"]').dataset.pysxRef !== window.token), true);
  assert.equal(await page.locator('[data-control="a"]').inputValue(), "typed draft");
  await page.evaluate(() => { window.staleHandler = document.querySelector('[data-pick="a"]').dataset.pysxClick; });
  await page.locator('#remove').click();
  await page.waitForFunction(() => !document.querySelector('[data-row="a"]'));
  assert.equal(await page.locator('[data-footer="a"]').count(), 0);
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("custom-keyed")));
  await page.waitForFunction(() => document.querySelector('#listened')?.textContent === '3');
  await page.locator('#restore').click();
  await page.waitForSelector('[data-control="a"]');
  assert.equal(await page.locator('[data-control="a"]').inputValue(), "Restored");
  assert.equal(await page.locator('[data-count="a"]').textContent(), "0");
  await page.waitForFunction(() => window.metrics.listen === 3);
  await page.locator('#clear').click();
  await page.waitForFunction(() => document.querySelector('#selected')?.textContent === 'none');
  await page.evaluate(() => {
    window.socketProbe.send(JSON.stringify({t: "event", h: window.staleHandler}));
    document.querySelector('[data-count="a"]').click();
  });
  await page.waitForFunction(() => document.querySelector('[data-count="a"]')?.textContent === '1');
  assert.equal(await page.locator('#selected').textContent(), "none");
  assert.deepEqual(errors, []);
  console.log("  ok  genuine remounts replace only changed controls and removed owners restart");
  console.log("KEYED BROWSER PASSED");
} finally { await browser.close(); }
