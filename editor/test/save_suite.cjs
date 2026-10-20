const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {spawn} = require("node:child_process");
const {createServer} = require("node:net");
const {once} = require("node:events");
const {createHash} = require("node:crypto");
const vscode = require("vscode");

async function until(predicate, label, timeout = 60000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await predicate();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error(`timeout waiting for ${label}`);
}

exports.run = async function () {
  const checkout = process.env.PYSX_EDITOR_CHECKOUT;
  const python = process.env.PYSX_EDITOR_PYTHON;
  const root = vscode.workspace.workspaceFolders[0].uri.fsPath;
  fs.cpSync(path.join(checkout, "examples"), path.join(root, "examples"), {
    recursive: true, filter: filename => !filename.includes("__pycache__"),
  });
  const filename = path.join(root, "examples", "counter.py");
  const settings = vscode.workspace.getConfiguration("pysx");
  await settings.update("pythonPath", python, vscode.ConfigurationTarget.Workspace);
  await settings.update("useLanguageServer", true, vscode.ConfigurationTarget.Workspace);
  const editor = vscode.workspace.getConfiguration("editor");
  // Exercise the import action on manual and focus-change saves. VSCode skips
  // save actions on delayed autosave even with "always"; cover that path too.
  await editor.update("codeActionsOnSave", {"source.organizeImports.pysx": "always"}, vscode.ConfigurationTarget.Workspace);
  await editor.update("formatOnSave", false, vscode.ConfigurationTarget.Workspace);
  const files = vscode.workspace.getConfiguration("files");
  await files.update("autoSave", "off", vscode.ConfigurationTarget.Workspace);
  const api = await vscode.extensions.getExtension("pysx-local.pysx-lang").activate();
  const document = await vscode.workspace.openTextDocument(filename);
  await vscode.window.showTextDocument(document);
  await until(async () => {
    if (!api.client) return false;
    try { return (await api.client.sendRequest("pysx/readiness", {uri: document.uri.toString()})).ready; }
    catch { return false; }
  }, "initial resident analysis");
  const resident = api.client;
  const send = resident.sendRequest.bind(resident);
  let importRequests = 0;
  resident.sendRequest = (method, ...args) => {
    if ((method === "textDocument/codeAction" || method?.method === "textDocument/codeAction")
      && args[0]?.context?.only?.includes("source.organizeImports.pysx")) importRequests++;
    return send(method, ...args);
  };

  const probe = createServer();
  probe.listen(0, "127.0.0.1");
  await once(probe, "listening");
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  const launcher = path.join(path.dirname(python), process.platform === "win32" ? "pysx.exe" : "pysx");
  const child = spawn(launcher, ["--app", "examples.counter:app", "--watch", "--port", String(port)], {
    cwd: root, env: {...process.env, PYTHONPATH: checkout}, stdio: ["ignore", "pipe", "pipe"],
  });
  const exited = once(child, "exit");
  let output = "";
  child.stdout.on("data", data => { output = (output + data).slice(-10000); });
  child.stderr.on("data", data => { output = (output + data).slice(-10000); });
  const samples = [];
  let browser;
  const record = (error = null) => {
    const report = {endpoint: "VSCode save command/autosave enable to didSave and foreground Chromium DOM", vscode: vscode.version,
      browser: browser?.version(), platform: process.platform, arch: process.arch,
      launcher, python, samples, error, passed: error === null && samples.length === 6, paint_qualification: false};
    const directory = path.join(checkout, ".artifacts", "dev");
    fs.mkdirSync(directory, {recursive: true});
    const data = JSON.stringify(report, null, 2) + "\n";
    fs.writeFileSync(path.join(directory, `editor-save-latency-${new Date().toISOString().replace(/[:.]/g, "-")}-${process.pid}.json`), data);
    fs.writeFileSync(path.join(directory, "editor-save-latency.json"), data);
  };
  const base = `http://127.0.0.1:${port}`;
  try {
    await until(async () => {
      assert.equal(child.exitCode, null, output);
      try { return (await fetch(base)).ok; } catch { return false; }
    }, "actual watch CLI startup");
    const {chromium} = require(path.join(checkout, "tests", "node_modules", "playwright"));
    browser = await chromium.launch({ignoreDefaultArgs: [
      "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
    ]});
    const page = await browser.newPage();
    await page.goto(base);
    const connected = () => page.waitForFunction(() => document.getElementById("pysx-root")?.dataset.pysxConnection === "connected");
    await connected();
    const background = await browser.newPage();
    const countSelector = 'pysx-slot[id="0"]';
    const value = expected => page.waitForFunction(({selector, expected}) => document.querySelector(selector)?.textContent === String(expected), {selector: countSelector, expected});
    await page.bringToFront();
    await page.getByRole("button", {name: "Increment", exact: true}).click();
    await value(1);
    let title = "Counter", initializer = 0;
    for (const mode of ["manual", "autosave-delay", "autosave-focus"]) {
      for (const edit of ["title", "initializer"]) {
        await files.update("autoSave", "off", vscode.ConfigurationTarget.Workspace);
        await background.bringToFront();
        await vscode.window.showTextDocument(document);
        const text = document.getText();
        const before = edit === "title" ? `Title: "${title}"` : `count = signal(${initializer})`;
        const after = edit === "title" ? `Title: "${mode} save"` : `count = signal(${1-initializer})`;
        if (edit === "title") title = `${mode} save`;
        else initializer = 1-initializer;
        const at = text.indexOf(before);
        assert.ok(at >= 0, before);
        let savedAt = null;
        const changed = vscode.workspace.onDidSaveTextDocument(saved => {
          if (saved.uri.toString() === document.uri.toString()) savedAt = performance.now();
        });
        try {
          const changes = new vscode.WorkspaceEdit();
          changes.replace(document.uri, new vscode.Range(document.positionAt(at), document.positionAt(at + before.length)), after);
          assert(await vscode.workspace.applyEdit(changes));
          assert(document.isDirty);
          const requestsBeforeSave = importRequests;
          const started = performance.now();
          if (mode === "manual") await vscode.commands.executeCommand("workbench.action.files.save");
          else if (mode === "autosave-delay") {
            await files.update("autoSaveDelay", 100, vscode.ConfigurationTarget.Workspace);
            await files.update("autoSave", "afterDelay", vscode.ConfigurationTarget.Workspace);
          } else {
            await files.update("autoSave", "onFocusChange", vscode.ConfigurationTarget.Workspace);
            await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(path.join(root, "examples", "README.md")));
          }
          await until(() => savedAt !== null, `${mode} disk save`, 5000);
          assert.ok(fs.readFileSync(filename, "utf8").includes(after));
          await page.bringToFront();
          if (edit === "title") await page.waitForFunction(title => document.querySelector("h1")?.textContent === title, `${mode} save`, {polling: "raf"});
          else await value(initializer);
          // An initializer reset to the same displayed number must still await
          // the actual new generation rather than timing an unchanged old DOM.
          await until(async () => {
            const status = await (await fetch(base + "/__pysx_dev__/status")).json();
            return status.generation > samples.length + 1 && status;
          }, "new worker generation");
          await connected();
          const visible = performance.now();
          const status = await (await fetch(base + "/__pysx_dev__/status")).json();
          const sample = {mode, edit, save_seconds: (savedAt-started)/1000, visible_seconds: (visible-started)/1000,
            source_sha256: createHash("sha256").update(fs.readFileSync(filename)).digest("hex"),
            import_requests: importRequests - requestsBeforeSave,
            generation: status.generation, reload_seconds: status.reload_seconds, stages: status.stages};
          samples.push(sample);
          console.log(`PYSX EDITOR SAVE: ${JSON.stringify(sample)}`);
          if (mode !== "autosave-delay") assert.ok(sample.import_requests > 0, `${mode} must exercise the resident import action`);
          assert.ok(sample.save_seconds < 1, `${mode} save stalled: ${JSON.stringify(sample)}`);
          assert.ok(sample.visible_seconds < 1.5, `${mode} update stalled: ${JSON.stringify(sample)}`);
        } finally { changed.dispose(); }
        // Let the page's restored signal differ from the next initializer.
        if (edit === "title" && mode === "manual") {
          await page.getByRole("button", {name: "Increment", exact: true}).click();
          await value(2);
        }
      }
    }
    record();
    console.log("PYSX EDITOR CASES PASSED: 6 (manual, delayed autosave and focus autosave title/initializer, actual CLI and DOM timings)");
  } catch (error) {
    record(String(error));
    throw error;
  } finally {
    await files.update("autoSave", "off", vscode.ConfigurationTarget.Workspace);
    if (browser) await browser.close();
    child.kill("SIGINT");
    const [code] = await exited;
    assert.equal(code, 0, output);
  }
};
