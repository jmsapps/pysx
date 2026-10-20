"""Explicitly composed state diagnostics for the development example."""

from pysx import Fragment, pysx

from .controls import Card, Description


def state_panel() -> Fragment:
    return pysx(t"""
        Card(id="pysx-dev-state", data-pysx-imperative="true"):
            h2: "Development snapshots"
            Description:
                "Workspace aggregates for component-local development snapshots. "
                "Declared root state above uses its separate SQLite store."
            p(id="pysx-dev-state-text", role="status"): "Waiting for development metadata."
            ul(id="pysx-dev-state-resets")
            a(id="pysx-dev-cold", href="/__pysx_dev__/cold"): "Discard development snapshots"
            Description:
                "Discard clears pending development handoffs and starts this page fresh. "
                "It does not delete the declared-state store or reset other live tabs."
    """)
