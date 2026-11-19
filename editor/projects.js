const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

// Mirrors pysx/compiler/projects.py: the innermost packaging marker owns the file, and
// the search never leaves the workspace folder that bounds it.
const MARKERS = ["pyproject.toml", "setup.cfg", "setup.py"];
const MAX_DEPTH = 64;

function projectRoot(file, bound) {
  let directory = path.resolve(path.dirname(file));
  const limit = bound ? path.resolve(bound) : null;
  if (limit && !(directory === limit || directory.startsWith(limit + path.sep))) return null;
  for (let depth = 0; depth < MAX_DEPTH; depth++) {
    if (MARKERS.some((marker) => fs.existsSync(path.join(directory, marker)))) return directory;
    if (directory === limit || path.dirname(directory) === directory) return null;
    directory = path.dirname(directory);
  }
  return null;
}

// The analysis root for a document: its own project where one exists, and otherwise the
// workspace folder, so unmarked folders keep their current behaviour.
function analysisRoot(vscode, uri, fileName) {
  const folder = vscode.workspace.getWorkspaceFolder(uri)?.uri.fsPath;
  if (!fileName) return folder ?? null;
  const fallback = folder ?? path.dirname(fileName);
  return projectRoot(fileName, folder) ?? fallback;
}

// Mirrors pysx/runtime/state_paths.py. Projections are disposable analysis output, so
// they live under the project's own `.pysx/` state root rather than beside the user's
// source, and the root ignores itself instead of editing the user's .gitignore.
const STATE_MARKER = ".pysx";

function stateComponent(namespace) {
  const cleaned = namespace.replace(/[^A-Za-z0-9._-]/g, "-").replace(/^[.-]+|[.-]+$/g, "");
  if (cleaned && cleaned === namespace && cleaned.length <= 64) return cleaned;
  const digest = crypto.createHash("sha256").update(namespace).digest("hex").slice(0, 16);
  const prefix = cleaned.slice(0, 32).replace(/^[.-]+|[.-]+$/g, "");
  return prefix ? `${prefix}-${digest}` : digest;
}

// A readable project name plus a digest of its full path: legible when a user opens
// `.pysx/`, still distinct when two projects share one configured state root.
function stateNamespace(root) {
  const digest = crypto.createHash("sha256").update(root).digest("hex").slice(0, 16);
  return `${path.basename(root)}-${digest}`;
}

function temporaryPath(root, namespace, owner) {
  const configured = process.env.PYSX_STATE_DIR;
  const base = configured ? path.resolve(configured) : path.join(root, STATE_MARKER);
  fs.mkdirSync(base, { recursive: true });
  if (path.basename(base) === STATE_MARKER) {
    const ignore = path.join(base, ".gitignore");
    if (!fs.existsSync(ignore)) fs.writeFileSync(ignore, "*\n");
  }
  const directory = path.join(base, stateComponent(namespace), "temporary", stateComponent(owner));
  fs.mkdirSync(directory, { recursive: true });
  return directory;
}

module.exports = {
  MARKERS, STATE_MARKER, projectRoot, analysisRoot, stateComponent,
  stateNamespace, temporaryPath,
};
