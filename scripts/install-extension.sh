#!/usr/bin/env bash
set -euo pipefail
unset VIRTUAL_ENV

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v code >/dev/null 2>&1; then
  echo "VSCode 'code' CLI not on PATH — skipping editor install."
  exit 0
fi

VSIX="$(uv run --quiet --project "$ROOT" python "$ROOT/editor/build_vsix.py")"
code --install-extension "$VSIX" --force
echo
echo "installed: pysx-local.pysx-lang"
echo "grammar changes require: Developer: Reload Window"
