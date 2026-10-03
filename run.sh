#!/usr/bin/env bash
set -euo pipefail

# A VIRTUAL_ENV exported by the parent shell makes uv print a mismatch warning
# above every line this script emits.
unset VIRTUAL_ENV

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PSX_PORT:-8750}"
APP="${PSX_APP:-pysx.examples.counter:app}"

# uv downloads interpreters and wheels silently under --quiet; without this the
# first run looks like a hang.
[ -d "$HERE/.venv" ] || echo "preparing environment (one time)..."

exec uv run --quiet --project "$HERE" python -m pysx.server \
  --app "$APP" --port "$PORT"
