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

module.exports = { MARKERS, projectRoot, analysisRoot };
