"""Watch the shared durable-state example with an isolated, stable local store.

Run `pysx --app examples.development:app --watch --inspect` from the repository.
Edit the shared view in durable_state.py while keeping two browser tabs open.
"""

import os
from pathlib import Path

from pysx import Fragment, Signal, StaticAsset, native

from .components.development import state_panel
from .durable_state import ExampleState
from .durable_state import create_application as create_application
from .durable_state import view as durable_view


def view(state: Signal[ExampleState]) -> Fragment:
    return native.Div(
        durable_view(state), state_panel(), native.Script(src="/development-panel.js", defer=True)
    )


state_path = Path(os.environ.get("PYSX_STATE_PATH", ".artifacts/development/state.sqlite3"))
state_path.parent.mkdir(parents=True, exist_ok=True)
app = create_application(state_path, render=view)
app.assets["/development-panel.js"] = StaticAsset.from_path(
    Path(__file__).parent / "components" / "development.js", content_type="text/javascript"
)
