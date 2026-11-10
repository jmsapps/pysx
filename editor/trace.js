const {createHmac, randomBytes} = require("node:crypto");
const {performance} = require("node:perf_hooks");

const LIMIT = 512;
const salt = randomBytes(32);
const events = [];
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
  events.push(Object.freeze({sequence: ++sequence, ms: performance.now(), component, event, ...fields}));
  if (events.length > LIMIT) { events.shift(); dropped++; }
}

function observe(component, operation, fields, action) {
  if (!enabled()) return action();
  const operationId = id("operation");
  const context = {...fields, operationId, operation};
  record(component, "begin", context);
  const failed = (error) => record(component, "failed", {...context,
    errorName: typeof error?.name === "string" ? error.name.slice(0, 80) : "unknown",
    errorCode: typeof error?.code === "number" ? error.code : undefined});
  try {
    const result = action();
    if (result && typeof result.then === "function") {
      // Observe without replacing the promise or changing its rejection for the caller.
      result.then(() => record(component, "completed", context), failed);
    } else record(component, "completed", context);
    return result;
  } catch (error) { failed(error); throw error; }
}

exports.id = id;
exports.resource = resource;
exports.record = record;
exports.observe = observe;
exports.snapshot = () => ({limit: LIMIT, dropped, events: events.slice()});
