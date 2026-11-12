const {createHmac, randomBytes} = require("node:crypto");
const {performance} = require("node:perf_hooks");
const {AsyncLocalStorage} = require("node:async_hooks");

const LIMIT = 512;
const salt = randomBytes(32);
const events = [];
const context = new AsyncLocalStorage();
let sequence = 0;
let identifiers = 0;
let dropped = 0;

const enabled = () => process.env.PYSX_EDITOR_TRACE === "1";
const id = (kind) => `${kind}-${++identifiers}`;
// A process-local salt allows correlation without retaining paths or an ID cache.
const resource = (value) => enabled()
  ? createHmac("sha256", salt).update(String(value)).digest("hex").slice(0, 16) : undefined;

function record(component, event, fields = {}) {
  if (!enabled()) return;
  events.push(Object.freeze({sequence: ++sequence, ms: performance.now(), component, event,
    parentOperationId: context.getStore(), ...fields}));
  if (events.length > LIMIT) { events.shift(); dropped++; }
}

function observe(component, operation, fields, action) {
  if (!enabled()) return action();
  const operationId = id("operation");
  const details = {...fields, operationId, operation, parentOperationId: context.getStore()};
  record(component, "begin", details);
  const failed = (error) => record(component, "failed", {...details,
    errorName: typeof error?.name === "string" ? error.name.slice(0, 80) : "unknown",
    errorCode: typeof error?.code === "number" ? error.code : undefined});
  try {
    const result = context.run(operationId, action);
    if (result && typeof result.then === "function") {
      // Observe without replacing the promise or changing its rejection for the caller.
      result.then(() => record(component, "completed", details), failed);
    } else record(component, "completed", details);
    return result;
  } catch (error) { failed(error); throw error; }
}

exports.id = id;
exports.resource = resource;
exports.record = record;
exports.observe = observe;
exports.snapshot = () => ({limit: LIMIT, dropped, events: events.slice()});

// A caught failure exports the timeline itself. Host death cancels the run before
// that catch, so the same evidence is also flushed synchronously when the process
// leaves, which "exit" reports for both ordinary returns and uncaught exceptions.
// It cannot observe SIGKILL, so a missing file still means an unobserved death.
exports.persist = (file) => {
  if (!enabled() || !file) return false;
  let flushed = false;
  process.on("exit", () => {
    if (flushed) return;
    flushed = true;
    try {
      require("node:fs").appendFileSync(file,
        "PYSX EDITOR EXIT TRACE " + JSON.stringify(exports.snapshot()) + "\n");
    } catch { /* evidence capture must never alter the exit it is recording */ }
  });

  return true;
};
