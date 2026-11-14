#!/usr/bin/env bash
set -euo pipefail
unset VIRTUAL_ENV

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v code >/dev/null 2>&1; then
  echo "VSCode 'code' CLI not on PATH — skipping editor install."
  exit 0
fi

VSIX="$(uv run --quiet --project "$ROOT" python "$ROOT/editor/build_vsix.py")"
if code --list-extensions | grep -i -x -q 'pysx-local.pysx-lang'; then
  code --uninstall-extension pysx-local.pysx-lang
fi
code --install-extension "$VSIX" --force
echo
echo "installed: jmsapps.pysx"
echo "takes effect after: Developer: Reload Window"
