#!/usr/bin/env bash
set -uo pipefail

# Uninstall by id first: extensions.json is authoritative, and removing the
# folder alone leaves a registered-but-missing extension.
code --uninstall-extension pysx-local.pysx-lang 2>/dev/null || true
rm -rf "$HOME"/.vscode/extensions/pysx-local.pysx-lang-* 2>/dev/null || true

echo "removed: pysx-local.pysx-lang"
