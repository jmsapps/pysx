import assert from "node:assert/strict";
import playwright from "playwright";

const browser = await playwright[process.env.PYSX_BROWSER_ENGINE].launch();
try {
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${process.argv[2]}`);
  await page.waitForSelector("#callable");
  const style = (id) => page.locator(id).evaluate((node) => {
    const css = getComputedStyle(node);
    return [css.color, css.backgroundColor, css.paddingLeft];
  });
  assert.deepEqual(await style("#red-blue"), ["rgb(0, 0, 255)", "rgb(255, 255, 255)", "11px"]);
  assert.deepEqual(await style("#blue-red"), ["rgb(255, 0, 0)", "rgb(0, 0, 0)", "13px"]);
  console.log("  ok  opposing lineages and pre-registration preserve cascade");
  assert.equal((await style("#third"))[2], "17px");
  console.log("  ok  repeated declarations and multiple inheritance levels");
  for (const id of ["#callable", "#second"]) {
    assert.equal((await style(id))[0], "rgb(0, 0, 255)");
    assert.equal((await style(id))[2], "19px");
  }
  assert.match(await page.locator("#callable").textContent(), /rootchild/);
  assert.equal(await page.locator("#callable strong").getAttribute("class"), null);
  console.log("  ok  callable props and children with all fragment roots exposed");
  await page.locator("#change").click();
  await page.waitForFunction(() => document.querySelector("#red-blue").classList.contains("dynamic"));
  assert.equal((await style("#red-blue"))[0], "rgb(0, 0, 255)");
  console.log("  ok  dynamic user class retains scoped lineage class");
  console.log("STYLED BROWSER PASSED");
} finally { await browser.close(); }
