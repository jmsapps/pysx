const assert = require("node:assert/strict");
const {test} = require("node:test");
const trace = require("../trace.js");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

test("trace preserves outcomes, privacy and bounded failure history", async () => {
  const original = process.env.PYSX_EDITOR_TRACE;
  try {
    delete process.env.PYSX_EDITOR_TRACE;
    const before = trace.snapshot().events.length;
    const disabled = Promise.resolve(3);
    assert.equal(trace.observe("test", "disabled", {}, () => disabled), disabled);
    assert.equal(trace.snapshot().events.length, before);
    process.env.PYSX_EDITOR_TRACE = "1";
    const key = trace.resource("/private/workspace/secret.py");
    assert.equal(trace.resource("/private/workspace/secret.py"), key);
    assert(!key.includes("secret"));
    const promise = Promise.resolve(7);
    assert.equal(trace.observe("test", "success", {resource: key}, () => promise), promise);
    await promise;
    const error = Object.assign(new Error("sensitive content"), {code: -32800});
    const rejected = Promise.reject(error);
    assert.equal(trace.observe("test", "reject", {}, () => rejected), rejected);
    await assert.rejects(rejected, (actual) => actual === error);
    assert.throws(() => trace.observe("test", "throw", {}, () => { throw error; }),
      (actual) => actual === error);
    const snapshot = trace.snapshot();
    const begin = snapshot.events.find(item => item.operation === "reject" && item.event === "begin");
    const failed = snapshot.events.find(item => item.operation === "reject" && item.event === "failed");
    assert.equal(begin.operationId, failed.operationId);
    assert.equal(failed.errorCode, -32800);
    assert(!JSON.stringify(snapshot).includes("sensitive content"));
    for (let n = 0; n < snapshot.limit + 10; n++) trace.record("test", "overflow", {n});
    const bounded = trace.snapshot();
    assert.equal(bounded.events.length, bounded.limit);
    assert(bounded.dropped > 0);
    assert(bounded.events.every((item, index, all) => index === 0 ||
      (item.sequence > all[index - 1].sequence && item.ms >= all[index - 1].ms)));
    bounded.events.pop();
    assert.equal(trace.snapshot().events.length, bounded.limit);
  } finally {
    if (original === undefined) delete process.env.PYSX_EDITOR_TRACE;
    else process.env.PYSX_EDITOR_TRACE = original;
  }
});

test("provider cancellation retains request identity and disposes its observer", async () => {
  const original = process.env.PYSX_EDITOR_TRACE;
  process.env.PYSX_EDITOR_TRACE = "1";
  try {
    let options;
    const exports = {};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../client.js"), "utf8"), {
      exports, process, setTimeout, clearTimeout,
      require: name => name === "vscode" ? {workspace: {
        createFileSystemWatcher: () => ({dispose() {}}), textDocuments: [],
      }} : name === "vscode-languageclient/node" ? {
        LanguageClient: class { constructor(_id, _name, _server, configuration) { options = configuration; } },
      } : name === "./trace.js" ? trace : require(name),
    });
    exports.createClient({python: "unused", output: {}, owns: () => true});
    let cancel;
    let disposed = 0;
    const token = {isCancellationRequested: false,
      onCancellationRequested: callback => { cancel = callback; return {dispose() { disposed++; }}; }};
    const document = {languageId: "python", uri: "file:///secret.py", version: 4};
    let reject;
    const promise = new Promise((_resolve, fail) => { reject = fail; });
    const returned = options.middleware.provideHover(document, {}, token, () => promise);
    assert.equal(returned, promise);
    token.isCancellationRequested = true;
    cancel();
    const error = Object.assign(new Error("Canceled"), {code: -32800});
    reject(error);
    await assert.rejects(returned, (actual) => actual === error);
    assert.equal(disposed, 1);
    const events = trace.snapshot().events;
    const canceled = events.findLast(item => item.event === "cancel-requested");
    const failed = events.findLast(item => item.event === "failed");
    assert.equal(canceled.requestId, failed.requestId);
    assert.equal(canceled.clientId, failed.clientId);
    assert.equal(failed.version, 4);
  } finally {
    if (original === undefined) delete process.env.PYSX_EDITOR_TRACE;
    else process.env.PYSX_EDITOR_TRACE = original;
  }
});

test("surface suite exports retained trace when setup fails and rethrows the failure", async () => {
  const original = process.env.PYSX_EDITOR_PYTHON;
  const tracing = process.env.PYSX_EDITOR_TRACE;
  delete process.env.PYSX_EDITOR_PYTHON;
  try {
    const exports = {};
    const output = [];
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "surface_suite.cjs"), "utf8"), {
      exports, process, console: {error: value => output.push(value)},
      require: name => name === "vscode" ? {extensions: {getExtension: () => ({
        extensionPath: path.resolve(__dirname, ".."),
      })}} : require(name),
    });
    await assert.rejects(exports.run(), /selected interpreter supplied/);
    const line = output.find(value => value.startsWith("PYSX EDITOR FAILURE TRACE "));
    assert(line, "setup failure must export the same trace used by the installed extension");
    assert(Array.isArray(JSON.parse(line.slice("PYSX EDITOR FAILURE TRACE ".length)).events));
  } finally {
    if (original === undefined) delete process.env.PYSX_EDITOR_PYTHON;
    else process.env.PYSX_EDITOR_PYTHON = original;
    if (tracing === undefined) delete process.env.PYSX_EDITOR_TRACE;
    else process.env.PYSX_EDITOR_TRACE = tracing;
  }
});
