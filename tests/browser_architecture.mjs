import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import playwright from "playwright";

const engine = process.env.PYSX_BROWSER_ENGINE;
if (!["chromium", "firefox", "webkit"].includes(engine)) throw new Error("missing proof engine");
if (process.platform === "darwin") delete process.env.DYLD_LIBRARY_PATH;
const selected = spawnSync("uv", ["run", "--project", ".", "python", "-c",
  "import json; from tests.prototypes.styled_lineage import cascade_fixture; print(json.dumps(cascade_fixture()))"],
  { encoding: "utf8", timeout: 30000 });
if (selected.status !== 0) throw new Error(`cascade fixture failed: ${selected.stderr}`);
const fixture = JSON.parse(selected.stdout);
const browser = await playwright[engine].launch();
try {
  const page = await browser.newPage();
  await page.setContent(`<style>${fixture.css}</style>
    <div id="red-blue" class="literal ${fixture.classes.red_blue}">A</div>
    <div id="blue-red" class="literal ${fixture.classes.blue_red}">B</div>`);
  const styles = async (id) => page.locator(`#${id}`).evaluate((element) => {
    const computed = getComputedStyle(element);
    return [computed.color, computed.backgroundColor, computed.paddingLeft];
  });
  assert.deepEqual(await styles("red-blue"), ["rgb(0, 0, 255)", "rgb(255, 255, 255)", "11px"]);
  console.log("  ok  red-to-blue lineage computes child color and retained base background");
  assert.deepEqual(await styles("blue-red"), ["rgb(255, 0, 0)", "rgb(0, 0, 0)", "13px"]);
  console.log("  ok  opposing blue-to-red lineage is independent of registration order");
  assert.equal(fixture.preregistered, fixture.classes.blue_red);
  assert.equal(await page.locator("style").evaluate((element) => element.sheet.cssRules.length), fixture.rule_count);
  console.log("  ok  identical pre-registered CSS is deduplicated without altering cascade");
  await page.locator("#red-blue").evaluate((element) => element.classList.replace("literal", "dynamic"));
  assert.deepEqual(await styles("red-blue"), ["rgb(0, 0, 255)", "rgb(255, 255, 255)", "11px"]);
  console.log("  ok  dynamic user-class merge retains lineage style");
  console.log("ARCHITECTURE CASCADE PASSED");
} finally {
  await browser.close();
}
