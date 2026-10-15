import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
import playwright from "playwright";

const root = fileURLToPath(new URL("../", import.meta.url));
// Browser bundles carry their own native libraries; GUI/toolchain overrides can
// otherwise make Firefox load an incompatible Homebrew NSS library on macOS.
if (process.platform === "darwin") delete process.env.DYLD_LIBRARY_PATH;
const args = process.argv.slice(2);
let suite = null;
for (let i = 0; i < args.length; i += 2) {
  if (args[i] !== "--suite" || !args[i + 1]) {
    throw new Error("expected --suite NAME");
  }
  suite = args[i + 1];
}
const registry = [
  { suite: "diagnostics_example", cases: [
    [0, "diagnostics", "browser_diagnostics_example.mjs", "DIAGNOSTICS EXAMPLE BROWSER PASSED"],
  ] },
  { suite: "diagnostics_actions", cases: [
    [0, "diagnostics_fixture", "browser_diagnostics.mjs", "DIAGNOSTICS ACTIONS BROWSER PASSED"],
  ] },
  { suite: "durable_state", cases: [
    [0, "durable_state_fixture", "browser_durable_state.mjs", "DURABLE STATE EXAMPLE BROWSER PASSED"],
  ] },
  { suite: "operational_topology", cases: [
    [0, "deployment_fixture", "browser_deployment.mjs", "OPERATIONAL TOPOLOGY BROWSER PASSED"],
  ] },
  { suite: "owner_routing", cases: [
    [0, "ownership_fixture", "browser_ownership.mjs", "OWNER ROUTING BROWSER PASSED"],
  ] },
  { suite: "session_security", cases: [
    [0, "session_security", "browser_session_security.mjs", "SESSION SECURITY EXAMPLE BROWSER PASSED"],
  ] },
  { suite: "deployment_security", cases: [
    [0, "tls_proxy", "browser_security_proxy.mjs", "DEPLOYMENT SECURITY BROWSER PASSED"],
  ] },
  { suite: "rendering_bootstrap", cases: [
    [0, "security_rendering", "browser_security_rendering.mjs", "RENDERING BOOTSTRAP BROWSER PASSED"],
  ] },
  { suite: "principal_origin", cases: [
    [0, "security_standalone", "browser_security.mjs", "PRINCIPAL ORIGIN BROWSER PASSED"],
    [1, "security_asgi", "browser_security.mjs", "PRINCIPAL ORIGIN BROWSER PASSED"],
  ] },
  { suite: "bounded_reconnect", cases: [
    [0, "reconnect_fixture", "browser_reconnect.mjs", "BOUNDED RECONNECT BROWSER PASSED"],
    [1, "expired_reconnect_fixture", "browser_reconnect.mjs", "BOUNDED RECONNECT BROWSER PASSED"],
  ] },
  { suite: "current_state", cases: [
    [0, "resume_fixture", "browser_resume.mjs", "CURRENT STATE BROWSER PASSED"],
  ] },
  { suite: "shape_protocol", cases: [
    [0, "counter", "browser_transport.mjs", "SHAPE PROTOCOL BROWSER PASSED"],
  ] },
  { suite: "resources", cases: [
    [0, "resources", "browser_resources.mjs", "RESOURCES EXAMPLE BROWSER PASSED"],
  ] },
  { suite: "host_config", cases: [
    [0, "host_standalone", "browser_hosts.mjs", "HOST CONFIG BROWSER PASSED"],
    [1, "host_asgi", "browser_hosts.mjs", "HOST CONFIG BROWSER PASSED"],
  ] },
  { suite: "serialized_async", cases: [
    [0, "async_fixture", "browser_async.mjs", "SERIALIZED ASYNC BROWSER PASSED"],
  ] },
  { suite: "meaningful_http", cases: [
    [0, "http_fixture", "browser_http.mjs", "MEANINGFUL HTTP BROWSER PASSED"],
  ] },
  { suite: "fallback_empty", cases: [
    [0, "recovery", "browser_fallback.mjs", "FALLBACK EXAMPLE BROWSER PASSED"],
    [1, "todos", "browser_empty.mjs", "EMPTY FILTER BROWSER PASSED"],
  ] },
  { suite: "recovery", cases: [
    [0, "recovery", "browser_fallback.mjs", "FALLBACK EXAMPLE BROWSER PASSED"],
  ] },
  { suite: "local_failure", cases: [
    [0, "recovery_fixture", "browser_recovery.mjs", "LOCAL FAILURE BROWSER PASSED"],
  ] },
  { suite: "boundary_error", cases: [
    [0, "counter", "browser_errors.mjs", "BOUNDARY ERROR BROWSER PASSED"],
  ] },
  { suite: "keyed_rows", cases: [
    [0, "keyed_rows", "browser_keyed_rows.mjs", "KEYED ROWS EXAMPLE BROWSER PASSED"],
  ] },
  { suite: "identity_cleanup", cases: [
    [0, "keyed_fixture", "browser_keyed.mjs", "KEYED BROWSER PASSED"],
    [1, "composition", "browser_tree.mjs", "TREE BROWSER PASSED"],
    [2, "keyed_snapshot_fixture", "browser_keyed_snapshot.mjs", "KEYED SNAPSHOT BROWSER PASSED"],
  ] },
  { suite: "granular_keyed", cases: [
    [0, "keyed_fixture", "browser_keyed.mjs", "KEYED BROWSER PASSED"],
  ] },
  { suite: "stable_paths", cases: [
    [0, "identity_fixture", "browser_identity.mjs", "IDENTITY BROWSER PASSED"],
  ] },
  { suite: "navigation_example", cases: [
    [0, "navigation", "browser_navigation_example.mjs", "NAVIGATION EXAMPLE BROWSER PASSED"],
  ] },
  { suite: "fragment_scrolling", cases: [
    [0, "fragments_fixture", "browser_fragments.mjs", "FRAGMENT BROWSER PASSED"],
  ] },
  { suite: "navigation_native", cases: [
    [0, "routing_fixture", "browser_navigation.mjs", "NAVIGATION BROWSER PASSED"],
  ] },
  { suite: "templates_example", cases: [
    [0, "templates", "browser_templates.mjs", "TEMPLATES BROWSER PASSED"],
  ] },
  { suite: "render_snapshot", cases: [
    [0, "control_fixture", "browser_control.mjs", "CONTROL BROWSER PASSED"],
    [0, "snapshot_fixture", "browser_snapshot.mjs", "SNAPSHOT BROWSER PASSED"],
  ] },
  { suite: "branches_loops", cases: [
    [0, "control_fixture", "browser_control.mjs", "CONTROL BROWSER PASSED"],
  ] },
  { suite: "styled_authoring", cases: [
    [0, "authoring_fixture", "browser_authoring.mjs", "AUTHORING BROWSER PASSED"],
    [0, "composition", "browser_tree.mjs", "TREE BROWSER PASSED"],
    [0, "themes", "browser_themes.mjs", "THEMES BROWSER PASSED"],
  ] },
  { suite: "components_example", cases: [
    [0, "composition", "browser_tree.mjs", "TREE BROWSER PASSED"],
  ] },
  { suite: "composition_recursive", cases: [
    [0, "tree_fixture", "browser_tree.mjs", "TREE BROWSER PASSED"],
  ] },
  { suite: "stable_component", cases: [
    [0, "components_fixture", "browser_components.mjs", "COMPONENTS BROWSER PASSED"],
  ] },
  { suite: "themes_example", cases: [
    [0, "themes", "browser_themes.mjs", "THEMES BROWSER PASSED"],
  ] },
  { suite: "styling_diagnostics", cases: [
    [0, "styled_fixture", "browser_styled.mjs", "STYLED BROWSER PASSED"],
    [1, "styles_fixture", "browser_styles.mjs", "STYLES BROWSER PASSED"],
  ] },
  { suite: "reactive_css", cases: [
    [0, "styles_fixture", "browser_styles.mjs", "STYLES BROWSER PASSED"],
  ] },
  { suite: "styled_bases", cases: [
    [0, "styled_fixture", "browser_styled.mjs", "STYLED BROWSER PASSED"],
  ] },
  { suite: "events_example", cases: [
    [0, "events", "browser_keyboard.mjs", "KEYBOARD BROWSER PASSED"],
  ] },
  { suite: "accessible_keyboard", cases: [
    [0, "keyboard_fixture", "browser_keyboard.mjs", "KEYBOARD BROWSER PASSED"],
  ] },
  { suite: "owned_dom", cases: [
    [0, "dom_fixture", "browser_dom.mjs", "DOM BROWSER PASSED"],
  ] },
  { suite: "events_immediate", cases: [
    [0, "events_fixture", "browser_events.mjs", "EVENTS BROWSER PASSED"],
  ] },
  { suite: "forms_example", cases: [
    [0, "forms", "browser_forms.mjs", "FORMS BROWSER PASSED"],
  ] },
  { suite: "bindings_form", cases: [
    [0, "forms_fixture", "browser_forms.mjs", "FORMS BROWSER PASSED"],
  ] },
  { suite: "serialization_live", cases: [
    [0, "native_fixture", "browser_serialization.mjs", "SERIALIZATION BROWSER PASSED"],
  ] },
  { suite: "example_reconnect", cases: [
    [0, "reconnect", "browser_example_reconnect.mjs", "EXAMPLE RECONNECT BROWSER PASSED"],
  ] },
  { suite: "examples", cases: [
    [0, "counter", "browser.mjs", "BROWSER ACCEPTANCE PASSED"],
    [1, "todos", "browser_todos.mjs", "TODOS BROWSER ACCEPTANCE PASSED"],
  ] },
  { suite: "cascade", cases: [
    [0, null, "browser_architecture.mjs", "ARCHITECTURE CASCADE PASSED"],
  ] },
  { suite: "adoption", cases: [
    [0, "adoption", "browser_adoption.mjs", "ARCHITECTURE ADOPTION PASSED"],
  ] },
  { suite: "template_ergonomics", cases: [
    [0, "reactive_state", "browser_operators.mjs", "OPERATORS BROWSER PASSED"],
  ] },
];
const selections = registry.filter((entry) => !suite || entry.suite === suite);
if (!selections.length) throw new Error("empty browser selection");

const children = new Set();
async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise((resolve) => child.once("exit", resolve));
  const kill = (signal) => {
    if (process.platform === "win32") {
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"]);
    } else {
      try { process.kill(-child.pid, signal); } catch (error) {
        if (!["ESRCH", "EPERM"].includes(error.code)) throw error;
      }
    }
  };
  kill("SIGTERM");
  const timer = setTimeout(() => kill("SIGKILL"), child.cleanupTimeout ?? 3000);
  await exited;
  clearTimeout(timer);
}
async function cleanup() { await Promise.all([...children].map(stop)); }
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => { void cleanup().finally(() => process.exit(1)); });
}
function start(command, argv, environment = {}) {
  const child = spawn(command, argv, {
    cwd: root, detached: process.platform !== "win32",
    env: { ...process.env, ...environment }, stdio: ["ignore", "pipe", "pipe"],
  });
  children.add(child);
  return child;
}
async function wait(child, ready = null, timeout = 30000) {
  let output = "";
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => finish(new Error(`child timeout: ${output}`)), timeout);
    function finish(error) {
      clearTimeout(timer);
      child.off("error", failed); child.off("close", closed);
      child.stdout.off("data", read); child.stderr.off("data", read);
      if (error) reject(error); else resolve();
    }
    const failed = (error) => finish(error);
    // "close" rather than "exit": the child's final stdout line is still
    // buffered when "exit" fires, and finish() detaches the data handlers.
    const closed = (code) => finish(code === 0 && !ready ? null : new Error(`child exited ${code}: ${output}`));
    const read = (data) => {
      output = (output + data.toString()).slice(-20000);
      if (ready && output.includes(ready)) finish();
    };
    child.on("error", failed); child.on("close", closed);
    child.stdout.on("data", read); child.stderr.on("data", read);
  });
  return output;
}
async function freePort(requested = 0) {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject); server.listen(requested, "127.0.0.1", resolve);
  });
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return port;
}
try {
  const python = start("uv", ["run", "--project", ".", "python", "-c", "import sys; print(sys.executable)"]);
  const interpreter = (await wait(python)).trim();
  if (!interpreter) throw new Error("missing selected interpreter");
  for (const engine of ["chromium", "firefox", "webkit"]) {
    // Preflight ensures an absent engine cannot silently reduce coverage.
    const browser = await playwright[engine].launch(); await browser.close();
  }
  let total = 0;
  let caseTotal = 0;
  for (const selection of selections) {
  const cases = selection.cases;
  let selectedAssertions = 0;
  for (const [index, name, script, banner] of cases) {
    const port = name ? await freePort(process.env.PYSX_BROWSER_PORT ? Number(process.env.PYSX_BROWSER_PORT) + index : 0) : 0;
    const server = name ? start(interpreter, name === "durable_state_fixture" ?
      ["tests/browser_deployment_fixture.py", "--port", String(port), "--example"] :
      name === "diagnostics_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_diagnostics_fixture:app", "--port", String(port)] :
      name === "deployment_fixture" ?
      ["tests/browser_deployment_fixture.py", "--port", String(port)] :
      name === "ownership_fixture" ?
      ["tests/browser_ownership_fixture.py", "--port", String(port)] :
      name === "tls_proxy" ?
      ["tests/browser_security_proxy_fixture.py", "--port", String(port)] :
      name === "security_rendering" ?
      ["-m", "pysx.server", "--app", "tests.browser_security_rendering_fixture:host", "--port", String(port)] :
      name?.startsWith("security_") ?
      ["-m", "pysx.server", "--app", "tests.browser_security_fixture:host", "--port", String(port), ...(name === "security_asgi" ? ["--asgi"] : [])] :
      name?.startsWith("host_") ?
      ["-m", "pysx.server", "--app", "tests.browser_host_fixture:host", "--port", String(port), ...(name === "host_asgi" ? ["--asgi"] : [])] :
      name === "keyed_snapshot_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_keyed_snapshot_fixture:app", "--port", String(port)] :
      name === "resume_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_resume_fixture:app", "--port", String(port)] :
      name === "reconnect_fixture" || name === "expired_reconnect_fixture" ?
      ["-m", "pysx.server", "--app", `tests.browser_reconnect_fixture:${name === "reconnect_fixture" ? "host" : "expired_host"}`, "--port", String(port)] :
      name === "http_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_http_fixture:app", "--port", String(port)] :
      name === "async_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_async_fixture:app", "--port", String(port)] :
      name === "keyed_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_keyed_fixture:app", "--port", String(port)] :
      name === "identity_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_identity_fixture:app", "--port", String(port)] :
      name === "keyboard_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_keyboard_fixture:app", "--port", String(port)] :
      name === "recovery_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_recovery_fixture:app", "--port", String(port)] :
      name === "routing_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_routing_fixture:app", "--port", String(port)] :
      name === "fragments_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_fragments_fixture:app", "--port", String(port)] :
      name === "components_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_components_fixture:app", "--port", String(port)] :
      name === "tree_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_tree_fixture:app", "--port", String(port)] :
      name === "snapshot_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_snapshot_fixture:app", "--port", String(port)] :
      name === "control_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_control_fixture:app", "--port", String(port)] :
      name === "authoring_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_authoring_fixture:app", "--port", String(port)] :
      name === "styled_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_styled_fixture:app", "--port", String(port)] :
      name === "styles_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_styles_fixture:app", "--port", String(port)] :
      name === "dom_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_dom_fixture:app", "--port", String(port)] :
      name === "events_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_events_fixture:app", "--port", String(port)] :
      name === "forms_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_forms_fixture:app", "--port", String(port)] :
      name === "native_fixture" ?
      ["-m", "pysx.server", "--app", "tests.browser_fixture:app", "--port", String(port)] :
      name === "adoption" ?
      ["-m", "tests.prototypes.standalone_host", "--port", String(port)] :
      ["run_example.py", "run", name, "--port", String(port)]) : null;
    if (server) {
      const deployment = name === "deployment_fixture" || name === "durable_state_fixture";
      if (deployment) server.cleanupTimeout = 45000;
      await wait(server, name === "host_asgi" || name === "security_asgi" ? "Uvicorn running" : "pysx ready", deployment ? 180000 : 15000);
    }
    for (const engine of ["chromium", "firefox", "webkit"]) {
      const suite = start(process.execPath, [`tests/${script}`, String(port)], { PYSX_BROWSER_ENGINE: engine, PYSX_BROWSER_CASE: name ?? "" });
      const output = await wait(suite, null, 60000);
      if (!output.includes(banner)) throw new Error(`missing suite banner: ${output}`);
      if (process.env.PYSX_BROWSER_FORCE_FAILURE === "1") throw new Error("forced assertion failure");
      const count = output.split("\n").filter((line) => line.startsWith("  ok  ")).length;
      if (!count) throw new Error("empty assertions");
      total += count;
      selectedAssertions += count;
      console.log(`${engine}: ${name ?? "cascade"}, 1 case, ${count} assertions`);
    }
    if (server) await stop(server);
  }
  await cleanup();
  caseTotal += cases.length * 3;
  console.log(`BROWSER VERIFICATION PASSED: ${selection.suite} (${cases.length * 3} cases, ${selectedAssertions} assertions)`);
  }
  if (selections.length > 1) console.log(`BROWSER VERIFICATION PASSED: all selected (${caseTotal} cases, ${total} assertions)`);
} catch (error) {
  console.error(error);
  await cleanup();
  process.exitCode = 1;
}
