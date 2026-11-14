#!/usr/bin/env bash
set -uo pipefail

# Uninstall by id and let VSCode maintain its own registry; sweeping the
# extensions folder by hand can hit another profile or a live installation.
# The pre-rename id is removed too, or the old build stays enabled.
code --uninstall-extension jmsapps.pysx 2>/dev/null || true
code --uninstall-extension pysx-local.pysx-lang 2>/dev/null || true

echo "removed: jmsapps.pysx (and any pysx-local.pysx-lang predecessor)"
echo "takes effect after: Developer: Reload Window"
