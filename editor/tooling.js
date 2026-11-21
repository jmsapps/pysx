// One managed tooling closure shared by both analysis paths.
//
// The application's own environment supplies PYSX, its runtime dependencies and the
// analysis implementation. Only third-party developer tooling is provisioned here, into
// extension-owned storage, so installing the extension never adds editor-only extras to
// an application's environment, manifests or lockfile.

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { execFile, execFileSync } = require("node:child_process");

// Exactly pinned, matching the project's `lsp` extra. A range would let a hijacked
// release reach client machines, so upgrades are deliberate and qualified.
const CLOSURE = Object.freeze({
  lsprotocol: "2025.0.0",
  pygls: "2.1.1",
  pyright: "1.1.414",
  ruff: "0.16.10",
});
const REQUIREMENTS = Object.freeze(
  Object.entries(CLOSURE).map(([name, version]) => `${name}==${version}`));
const MARKER = "closure.json";
const INSTALL_TIMEOUT_MS = 600000;

class ToolingError extends Error {
  constructor(message, { actionable = true } = {}) {
    super(message);
    this.name = "ToolingError";
    this.actionable = actionable;
  }
}

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    execFile(command, args, { timeout: INSTALL_TIMEOUT_MS, maxBuffer: 64 * 1024 * 1024, ...options },
      (error, stdout, stderr) => {
        if (error) reject(new ToolingError(`${path.basename(command)} failed: ${stderr || error.message}`));
        else resolve({ stdout, stderr });
      });
  });
}

function describe(interpreter) {
  // The closure is keyed by what actually affects wheel selection, so one machine can
  // hold closures for several applications without them colliding or being reused wrongly.
  const script = "import json, sys, sysconfig; print(json.dumps({" +
    "'version': '%d.%d' % sys.version_info[:2]," +
    "'implementation': sys.implementation.name," +
    "'platform': sysconfig.get_platform()}))";
  const output = execFileSync(interpreter, ["-c", script], { encoding: "utf8", timeout: 60000 });
  return JSON.parse(output);
}

function closureKey(interpreter) {
  const identity = describe(interpreter);
  const digest = crypto.createHash("sha256")
    .update(JSON.stringify({ requirements: REQUIREMENTS, ...identity })).digest("hex");
  return { key: digest.slice(0, 16), identity };
}

function toolPath(root, name) {
  const directory = path.join(root, process.platform === "win32" ? "Scripts" : "bin");
  const candidates = process.platform === "win32" ? [`${name}.exe`, name] : [name];
  for (const candidate of candidates) {
    const found = path.join(directory, candidate);
    if (fs.existsSync(found)) return found;
  }
  return null;
}

// A closure counts as present only when its marker records the same pins and identity and
// every tool it promises still exists; a half-written or pruned cache is reprovisioned
// rather than trusted.
function verify(root, key, identity) {
  const marker = path.join(root, MARKER);
  if (!fs.existsSync(marker)) return null;
  let recorded;
  try { recorded = JSON.parse(fs.readFileSync(marker, "utf8")); }
  catch { return null; }
  if (recorded.key !== key) return null;
  if (JSON.stringify(recorded.requirements) !== JSON.stringify(REQUIREMENTS)) return null;
  if (JSON.stringify(recorded.identity) !== JSON.stringify(identity)) return null;
  const tools = { ruff: toolPath(root, "ruff"), pyright: toolPath(root, "pyright") };
  if (!tools.ruff || !tools.pyright) return null;
  if (!fs.existsSync(path.join(root, "lib")) && !fs.existsSync(path.join(root, "Lib"))) return null;
  return { root, key, identity, tools, requirements: REQUIREMENTS, site: sitePackages(root) };
}

function sitePackages(root) {
  const base = process.platform === "win32" ? path.join(root, "Lib", "site-packages")
    : path.join(root, "lib");
  if (process.platform === "win32") return fs.existsSync(base) ? [base] : [];
  if (!fs.existsSync(base)) return [];
  return fs.readdirSync(base)
    .map((entry) => path.join(base, entry, "site-packages"))
    .filter((entry) => fs.existsSync(entry));
}

async function qualifiedUv(uv) {
  if (!uv) return null;
  const resolved = path.isAbsolute(uv) ? uv : null;
  // A bare name is deliberately not accepted: the GUI PATH is not the shell PATH, so a
  // name that resolves in a terminal can be absent when VSCode launches the extension.
  if (!resolved || !fs.existsSync(resolved)) return null;
  try {
    await run(resolved, ["--version"], { timeout: 30000 });
    return resolved;
  } catch { return null; }
}

// The closure is a real environment built by the *selected* interpreter, so its wheels
// match the Python that will import them. Installation never runs against application
// Python's own site-packages.
//
// It is built at its final path rather than staged and renamed: installers bake absolute
// shebangs into console scripts, so a rename leaves every tool pointing at a directory
// that no longer exists. The marker file, written last, is what makes the result atomic —
// an interrupted install leaves no marker and is reprovisioned rather than trusted.
async function provision(options) {
  const { storage, interpreter, uv = null, offline = false, onProgress = () => {} } = options;
  if (!fs.existsSync(interpreter)) {
    throw new ToolingError(`The selected interpreter ${interpreter} does not exist.`);
  }
  const { key, identity } = closureKey(interpreter);
  const root = path.join(storage, "tooling", key);
  const existing = verify(root, key, identity);
  if (existing) return { ...existing, provisioned: false };
  if (offline) {
    throw new ToolingError(
      "PYSX tooling is not cached for this interpreter and the editor is offline. " +
      "Reconnect and run `PYSX: Set Up Template Tooling`, or set `pysx.toolingPath` " +
      "to a compatible existing installation.");
  }
  fs.mkdirSync(path.dirname(root), { recursive: true });
  const lock = `${root}.lock`;
  let held = false;
  try { fs.mkdirSync(lock); held = true; }
  catch (error) {
    if (error.code !== "EEXIST") throw error;
    // Another window is provisioning the same closure. Wait for its marker rather than
    // racing it, so a multi-folder workspace does not install the same tools N times.
    const deadline = Date.now() + INSTALL_TIMEOUT_MS;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 250));
      const shared = verify(root, key, identity);
      if (shared) return { ...shared, provisioned: false, waited: true };
      if (!fs.existsSync(lock)) break;
    }
    const settled = verify(root, key, identity);
    if (settled) return { ...settled, provisioned: false, waited: true };
    throw new ToolingError("Another window's PYSX tooling setup did not finish. Retry setup.");
  }

  const resolvedUv = await qualifiedUv(uv);
  try {
    onProgress("Creating the PYSX tooling environment");
    fs.rmSync(root, { recursive: true, force: true });
    if (resolvedUv) {
      await run(resolvedUv, ["venv", "--python", interpreter, root]);
      await run(resolvedUv, ["pip", "install", "--python", pythonIn(root), ...REQUIREMENTS]);
    } else {
      // `ensurepip` lives in the standard library, so this path needs nothing installed
      // in the application environment and never writes to it.
      await run(interpreter, ["-m", "venv", root]);
      const python = pythonIn(root);
      await run(python, ["-m", "ensurepip", "--upgrade"]);
      await run(python, ["-m", "pip", "install", "--no-input", "--disable-pip-version-check",
        ...REQUIREMENTS]);
    }
    onProgress("Qualifying the provisioned tooling");
    const tools = { ruff: toolPath(root, "ruff"), pyright: toolPath(root, "pyright") };
    if (!tools.ruff || !tools.pyright) {
      throw new ToolingError("The provisioned PYSX tooling did not contain the expected tools.");
    }
    // Qualify by running each tool, not by its presence: a broken console script is
    // exactly the failure a path check cannot see.
    const { stdout: ruffVersion } = await run(tools.ruff, ["--version"], { timeout: 60000 });
    if (!ruffVersion.includes(CLOSURE.ruff)) {
      throw new ToolingError(`Provisioned Ruff reported ${ruffVersion.trim()}, expected ${CLOSURE.ruff}.`);
    }
    fs.writeFileSync(path.join(root, MARKER),
      JSON.stringify({ key, identity, requirements: REQUIREMENTS }, null, 2) + "\n");
  } catch (error) {
    fs.rmSync(root, { recursive: true, force: true });
    throw error;
  } finally {
    if (held) fs.rmSync(lock, { recursive: true, force: true });
  }
  const verified = verify(root, key, identity);
  if (!verified) {
    fs.rmSync(root, { recursive: true, force: true });
    throw new ToolingError("The provisioned PYSX tooling did not qualify.");
  }
  return { ...verified, provisioned: true, installer: resolvedUv ? "uv" : "ensurepip" };
}

function pythonIn(root) {
  return path.join(root, process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
}

// Parent `sys.path` does not propagate, so every Python child is launched with the
// closure named explicitly. PYTHONPATH is used rather than a site directory: entries on
// it precede the application's own site-packages, giving the pinned tooling priority over
// a conflicting copy, and `.pth` hooks in those directories are never executed.
function childEnvironment(closure, base = process.env) {
  const separator = process.platform === "win32" ? ";" : ":";
  const existing = base.PYTHONPATH ? [base.PYTHONPATH] : [];
  const environment = {
    ...base,
    PYTHONPATH: [...closure.site, ...existing].join(separator),
    PYSX_MANAGED_TOOLING: closure.root,
  };
  const node = closure.node ?? null;
  if (node) environment.PYSX_MANAGED_NODE = node;
  return environment;
}

module.exports = { CLOSURE, childEnvironment, REQUIREMENTS, MARKER, ToolingError, closureKey, provision, verify, toolPath, sitePackages, pythonIn };
