"""Callable composition, recursive keyboard controls and owned component state."""

from pysx import Fragment, html

from .components import Description, Eyebrow, Page, Title
from .components.composition import tree_controls


def app() -> Fragment:

    return html(
        t"""
            Page(id="composition-example"):
              Eyebrow: "Component ownership"
              Title: "A recursive component tree"
              Description: "Click a node or press Tab to focus the tree before using arrow keys."
              Description: "Right opens a branch; Left closes it or moves to its parent."
              Description: "Up/Down moves focus. Home/End reaches the first/last visible node."
              Description: "Enter activates a node. Reverse roots preserves its local count."
              Description: "Setup, browser mount and cleanup counters show separate lifetimes."
              tree_controls:
        """,
        use=(Page, Eyebrow, Title, Description, tree_controls),
    )
