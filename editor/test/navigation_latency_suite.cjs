const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vscode = require("vscode");
const timing = require("./navigation_timing.cjs");

const APPLICATION = process.env.PYSX_EDITOR_APPLICATION;
const VIEW = path.join(APPLICATION, "app", "containers", "tutorial", "view.py");
const CONTROLS = path.join(APPLICATION, "app", "components", "controls", "__init__.py");
const COLD_SAMPLES = 5;
const READY_SAMPLES = 20;

async function until(predicate, label, timeout = 90000) {
  const started = Date.now();
  let last;
  while (Date.now() < started + timeout) {
    last = await predicate();
    if (last) return last;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`timeout waiting for ${label} after ${((Date.now() - started) / 1000).toFixed(1)}s`);
}

exports.run = async function (mode, progress) {
  const clock = () => Number(process.hrtime.bigint() / 1000n) / 1000;
  const controls = await vscode.workspace.openTextDocument(CONTROLS);
  const buttonLine = controls.positionAt(controls.getText().indexOf("Button = styled")).line;
  const document = await vscode.workspace.openTextDocument(VIEW);
  await vscode.window.showTextDocument(document);
  // The site is re-derived outside every timed interval, so an edit that shifts the file
  // cannot turn a stale position into a false "no definition" result.
  const locate = () => {
    const text = document.getText();
    return document.positionAt(text.indexOf("Button(id=", text.indexOf("pysx(t")) + 1);
  };
  let site = locate();

  const request = () => vscode.commands.executeCommand(
    "vscode.executeDefinitionProvider", document.uri, site);
  // Every timed request is verified; a run that measures a wrong destination is a failure.
  const expect = (found) => {
    const file = fs.realpathSync.native((found.uri ?? found.targetUri).fsPath);
    const range = found.range ?? found.targetSelectionRange ?? found.targetRange;
    if (file !== fs.realpathSync.native(CONTROLS)) return `destination ${file}`;
    if (range.start.line !== buttonLine) return `line ${range.start.line} != ${buttonLine}`;
    return null;
  };
  const ready = () => until(async () => {
    const found = await request();
    return found && found.length && !expect(found[0]) ? found : null;
  }, "an analysed definition before timing begins");

  const scenarios = [];
  // Pending analysis: the first request after activation, before any readiness signal.
  progress(`${mode}: pending-analysis request`);
  scenarios.push(await timing.scenario({
    label: "pending-analysis", repetitions: 1, clock, request, expect,
  }));

  progress(`${mode}: cold samples`);
  await ready();
  // A cold sample must not reuse a warm analysis. The resident adapter exposes a restart
  // command; the worker's explicit refresh replaces its snapshot even for unchanged text.
  // Await that operation: a watched-source rewrite can return an old successful definition
  // before the watcher invalidates it, then race the actual timed request with the rebuild.
  const invalidate = mode === "worker"
    ? async () => { assert(await vscode.commands.executeCommand("pysx.refresh"), "cold projection refresh"); }
    : async () => { await vscode.commands.executeCommand("pysx.restartServer").then(() => {}, () => {}); };
  const coldInduction = mode === "worker" ? "pysx.refresh" : "pysx.restartServer";
  scenarios.push(await timing.scenario({
    label: "cold", repetitions: COLD_SAMPLES, clock, request, expect,
    before: async () => { await invalidate(); await ready(); },
  }));

  progress(`${mode}: ready-state samples`);
  // Ready state means settled, not merely "one request worked": the worker rebuilds
  // asynchronously after the cold scenario's invalidation, and a rebuild landing mid-run
  // would otherwise surface as sporadic empty results inside the timed samples.
  await until(async () => {
    // The worker debounces its rebuild, so the probe has to span that window rather
    // than complete inside it; five immediate successes prove nothing about settledness.
    for (let index = 0; index < 5; index++) {
      const found = await request();
      if (!found || !found.length || expect(found[0])) return null;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    return true;
  }, "five settled definitions spanning the rebuild debounce");
  scenarios.push(await timing.scenario({
    label: "ready", repetitions: READY_SAMPLES, warmup: 2, clock, request, expect,
  }));

  progress(`${mode}: post-invalidation request`);
  const editor = vscode.window.activeTextEditor;
  await editor.edit((builder) => builder.insert(document.positionAt(0), "# edited\n"));
  site = locate();
  scenarios.push(await timing.scenario({
    label: "post-invalidation", repetitions: 1, clock, request, expect,
  }));

  const run = {
    context: {
      mode,
      workspace: vscode.workspace.getWorkspaceFolder(document.uri).name,
      vscode: vscode.version,
      platform: process.platform,
      fixture_sources: fs.readdirSync(path.join(APPLICATION, "app", "components", "controls")).length,
      client: vscode.extensions.getExtension("jmsapps.pysx").packageJSON.version,
      cold_induction: coldInduction,
      cold_samples: COLD_SAMPLES,
      ready_samples: READY_SAMPLES,
    },
    scenarios,
  };
  console.log(timing.report(run));
  const destination = process.env.PYSX_NAVIGATION_RESULTS;
  if (destination) {
    const existing = fs.existsSync(destination)
      ? JSON.parse(fs.readFileSync(destination, "utf8")) : [];
    existing.push(run);
    fs.writeFileSync(destination, JSON.stringify(existing, null, 2) + "\n");
  }
  // The baseline is the deliverable; its speed is not an acceptance criterion. Only
  // incorrect destinations, empty results and timeouts fail the gate.
  for (const item of scenarios) {
    assert.equal(item.failures, 0,
      `${item.label} recorded failures: ${JSON.stringify(item.samples.filter((s) => !s.ok))}`);
  }
  await vscode.commands.executeCommand("workbench.action.closeAllEditors");
  return scenarios.map((item) => item.label);
};
