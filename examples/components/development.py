"""Explicitly composed state diagnostics for the development example."""

from pysx import Fragment, Signal, pysx

from .controls import Card, Description


def state_panel(count: Signal[int]) -> Fragment:
    return pysx(t"""
        Card(id="pysx-dev-state"):
            h2: "Development snapshots"
            Description:
                "This tab's sample count: "
                span(id="development-state-count"): {count}
            div(data-pysx-imperative="true"):
                p(id="pysx-dev-state-text", role="status"): "Waiting for development metadata."
                ul(id="pysx-dev-state-resets")
                a(id="pysx-dev-cold", href="/__pysx_dev__/cold"): "Discard development snapshots"
            Description:
                "The report shows workspace aggregates and reset reasons, "
                "without private state values. "
                "Discard clears pending development handoffs and starts this page fresh. "
                "Other live tabs keep their current state. No database is used."
    """)
