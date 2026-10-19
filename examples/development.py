"""Watch the shared durable-state example with an isolated, stable local store.

Run `pysx --app examples.development:app --watch --inspect` from the repository.
Edit the shared view in durable_state.py while keeping two browser tabs open.
"""

import os
from pathlib import Path

from .durable_state import create_application as create_application

state_path = Path(os.environ.get("PYSX_STATE_PATH", ".artifacts/development/state.sqlite3"))
state_path.parent.mkdir(parents=True, exist_ok=True)
app = create_application(state_path)
