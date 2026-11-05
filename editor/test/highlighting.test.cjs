const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

test("markup refresh retains decorations until a current response replaces them", async () => {
  const events = {};
  const listen = (name) => (handler) => {
    events[name] = handler;
    return { dispose() {} };
  };
  const uri = { toString: () => "file:///view.py" };
  const document = { uri, languageId: "python", version: 1, isClosed: false };
  uri.scheme = "file";
  const applied = new Map();
  const changes = [];
  const timers = new Set();
  const requests = [];
  const warnings = [];
  const vscode = {
    ThemeColor: class { constructor(id) { this.id = id; } },
    Range: class { constructor(...positions) { this.positions = positions; } },
    window: {
      createTextEditorDecorationType: ({ color }) => ({ kind: color.id, dispose() {} }),
      visibleTextEditors: [{
        document,
        setDecorations(type, ranges) {
          applied.set(type.kind, ranges);
          changes.push({ kind: type.kind, size: ranges.length });
        },
      }],
      onDidChangeVisibleTextEditors: listen("visible"),
    },
    workspace: {
      textDocuments: [document],
      onDidChangeTextDocument: listen("change"),
      onDidOpenTextDocument: listen("open"),
      onDidCloseTextDocument: listen("close"),
    },
  };
  const exports = {};
  vm.runInNewContext(readFileSync(path.join(__dirname, "../highlighting.js"), "utf8"), {
    exports,
    require: (name) => { assert.equal(name, "vscode"); return vscode; },
    setTimeout: (callback) => { timers.add(callback); return callback; },
    clearTimeout: (callback) => timers.delete(callback),
  });
  const controller = exports.activate({
    onNotification: (_, handler) => listen("analysis")(handler),
    sendRequest: () => new Promise((resolve, reject) => requests.push({ resolve, reject })),
  }, { warn: (message) => warnings.push(message) });
  const token = { kind: "class", range: {
    start: { line: 0, character: 0 }, end: { line: 0, character: 5 },
  } };
  const reply = (version, tokens = [token]) => ({ version, generation: version, tokens });
  const count = () => applied.get("pysx.markup.class")?.length;
  const initial = controller.refresh(document);
  requests.shift().resolve(reply(1));
  await initial;
  assert.equal(count(), 1);
  changes.length = 0;

  document.version = 2;
  events.change({ document });
  events.analysis({ uris: [uri.toString()] });
  assert.equal(changes.length, 0, "edit/save invalidation must not erase visible colors");
  assert.equal(timers.size, 1, "duplicate notifications coalesce");
  for (const callback of [...timers]) { timers.delete(callback); callback(); }
  assert.equal(changes.length, 0, "request in flight must preserve colors");
  const superseded = requests.shift();
  const latest = controller.refresh(document);
  superseded.resolve(reply(2, []));
  await Promise.resolve();
  assert.equal(count(), 1, "superseded empty response must not erase colors");
  requests.shift().resolve(reply(2));
  await latest;
  assert.equal(count(), 1);

  const failed = controller.refresh(document);
  requests.shift().reject(new Error("temporary analysis failure"));
  await failed;
  assert.equal(warnings.length, 1);
  assert.equal(count(), 1, "failed requests preserve colors");
  const empty = controller.refresh(document);
  requests.shift().resolve(reply(2, []));
  await empty;
  assert.equal(count(), 0, "a current empty result removes obsolete colors");

  const restore = controller.refresh(document);
  requests.shift().resolve(reply(2));
  await restore;
  const pending = controller.refresh(document);
  controller.pause();
  assert.equal(count(), 0, "pause clears colors immediately");
  requests.shift().resolve(reply(2));
  await pending;
  assert.equal(count(), 0, "late response cannot repaint after pause");
  controller.resume();
  const resumed = controller.refresh(document);
  requests.shift().resolve(reply(2));
  await resumed;
  document.languageId = "plaintext";
  events.change({ document });
  assert.equal(count(), 0, "ineligible documents clear colors immediately");
  events.close(document);
  controller.dispose();
  assert.equal(timers.size, 0);
});
