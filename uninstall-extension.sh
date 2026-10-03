#!/usr/bin/env bash
set -uo pipefail

# Uninstall by id first: extensions.json is authoritative, and removing the
# folder alone leaves a registered-but-missing extension.
code --uninstall-extension pysx-local.pysx-lang 2>/dev/null || true
rm -rf "$HOME"/.vscode/extensions/pysx-local.pysx-lang-* 2>/dev/null || true
# The repo-root settings file lives outside pysx/, so `rm -rf pysx/` cannot
# reach it. Only remove it if pysx wrote it.
ROOT_SETTINGS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.vscode/settings.json"
if [ -f "$ROOT_SETTINGS" ] && grep -q "written by pysx" "$ROOT_SETTINGS"; then
  rm -f "$ROOT_SETTINGS"
  rmdir "$(dirname "$ROOT_SETTINGS")" 2>/dev/null || true
  echo "removed: repo-root .vscode/settings.json"
fi

echo "removed: pysx-local.pysx-lang"
