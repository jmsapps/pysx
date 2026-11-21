// Navigation latency measurement, kept free of VSCode so its boundaries can be asserted
// deterministically. The suite injects a real clock and a real provider request; the
// deterministic tests inject a controlled clock and a scripted one.

const DEFAULT_TIMEOUT_MS = 15000;

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function summarize(samples) {
  const timings = samples.filter((item) => item.ok).map((item) => item.ms);
  return {
    count: samples.length,
    ok: timings.length,
    failures: samples.length - timings.length,
    median_ms: median(timings),
    min_ms: timings.length ? Math.min(...timings) : null,
    max_ms: timings.length ? Math.max(...timings) : null,
  };
}

// One timed request. The interval starts immediately before the provider is invoked and
// ends when its result resolves; target verification happens outside that interval, and a
// wrong destination is a failure rather than a discarded sample.
async function sample(options) {
  const { clock, request, expect, label, timeout = DEFAULT_TIMEOUT_MS } = options;
  const started = clock();
  let result;
  let error = null;
  try {
    result = await Promise.race([
      Promise.resolve().then(request),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error(`navigation timed out after ${timeout}ms`)), timeout)),
    ]);
  } catch (caught) {
    error = caught;
  }
  const ms = clock() - started;
  if (error) return { label, ok: false, ms, reason: "timeout", detail: error.message };
  const destination = result && result.length ? result[0] : null;
  if (!destination) return { label, ok: false, ms, reason: "empty" };
  const mismatch = expect ? expect(destination) : null;
  if (mismatch) return { label, ok: false, ms, reason: "wrong-target", detail: mismatch };
  return { label, ok: true, ms };
}

async function scenario(options) {
  const { repetitions, warmup = 0, before, ...rest } = options;
  for (let index = 0; index < warmup; index++) {
    if (before) await before(index);
    await sample(rest);
  }
  const samples = [];
  for (let index = 0; index < repetitions; index++) {
    if (before) await before(index);
    samples.push(await sample(rest));
  }
  return { label: options.label, samples, ...summarize(samples) };
}

function report(run) {
  const lines = [`PYSX NAVIGATION: ${run.context.mode}/${run.context.workspace}`];
  for (const item of run.scenarios) {
    const value = (name) => (item[name] === null ? "n/a" : `${item[name].toFixed(1)}ms`);
    lines.push(`  ${item.label}: n=${item.samples.length} ok=${item.ok} ` +
      `failures=${item.failures} median=${value("median_ms")} ` +
      `min=${value("min_ms")} max=${value("max_ms")}`);
  }
  return lines.join("\n");
}

module.exports = { DEFAULT_TIMEOUT_MS, median, summarize, sample, scenario, report };
