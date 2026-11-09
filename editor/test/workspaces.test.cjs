const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {test} = require("node:test");
const vm = require("node:vm");

const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};

for (const detached of [false, true]) {
  for (const notification of ["configuration", "environment"]) {
    for (const unavailable of [false, true]) {
      test(`selection during disposal: ${notification}, detached=${detached}, unavailable=${unavailable}`, async () => {
        const root = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "pysx-router-")));
        const uri = {scheme: "file", fsPath: root};
        const document = {languageId: "python", uri: {scheme: "file", fsPath: path.join(root, "view.py")}};
        const folder = {name: "workspace", uri};
        const executables = ["first", "intermediate", "latest"].map(name => path.join(root, name));
        for (const executable of executables) fs.writeFileSync(executable, "fixture");
        let selected = executables[0];
        const events = {};
        const subscribe = name => callback => { events[name] = callback; return {dispose() {}}; };
        const vscode = {
          workspace: {
            workspaceFolders: detached ? [] : [folder], textDocuments: [document],
            onDidChangeWorkspaceFolders: subscribe("folders"),
            onDidChangeConfiguration: subscribe("configuration"),
            onDidOpenTextDocument: subscribe("open"),
          },
          extensions: {getExtension: () => ({activate: async () => ({
            environments: {onDidChangeActiveEnvironmentPath: subscribe("environment")},
          })})},
        };
        const timers = new Map();
        let timerId = 0;
        const exports = {};
        vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../workspaces.js"), "utf8"), {
          exports, require: name => name === "vscode" ? vscode : require(name),
          setTimeout(callback) { const id = ++timerId; timers.set(id, callback); return id; },
          clearTimeout(id) { timers.delete(id); },
        });
        const flush = () => {
          const callbacks = [...timers.values()]; timers.clear();
          for (const callback of callbacks) callback();
        };
        const disposing = deferred();
        const release = deferred();
        const starts = [];
        let initialNotice = true;
        const router = exports.createRouter({
          output: {appendLine() {}, error() {}},
          resolveInterpreter: async () => selected,
          probeEnvironment: async (python, args) => {
            if (initialNotice) { initialNotice = false; changed(); }
            return unavailable && python === executables[1] ? null : args[0] === "-c" ? "ok" : "{}";
          },
          createClient: ({python}) => ({
            start: async () => { starts.push(python); },
            status: () => ({running: true}),
            dispose: async () => {
              if (python === executables[0]) { disposing.resolve(); await release.promise; }
            },
          }),
        });
        const changed = () => notification === "configuration"
          ? events.configuration({affectsConfiguration: () => true})
          : events.environment({resource: detached ? undefined : folder});
        try {
          let activated = false;
          let activatedPython;
          const starting = router.start().then(() => {
            activated = true; activatedPython = router.owner(document.uri)?.python;
          });
          for (let turn = 0; !activated && turn < 10; turn++) { await tick(); flush(); }
          assert(activated, "initial selections must settle in bounded event-loop turns");
          await starting;
          assert.equal(activatedPython, executables[0], "activation must return a ready initial client");
          assert.equal(router.owner(document.uri).python, executables[0]);
          selected = executables[1]; changed(); flush();
          await disposing.promise;
          assert.equal(router.owner(document.uri), null, "the old entry is temporarily removed");
          selected = executables[2]; changed();
          assert.equal(timers.size, 1, "selection must be retained while its client entry is absent");
          flush(); release.resolve(); await tick(); await tick();
          assert.equal(router.owner(document.uri).python, executables[2]);
          assert.equal(router.owner(document.uri).error, null);
          assert.deepEqual(starts, [executables[0], executables[2]], "a stale replacement never starts");
        } finally {
          release.resolve(); await router.dispose();
          fs.rmSync(root, {recursive: true, force: true});
        }
      });
    }
  }
}
