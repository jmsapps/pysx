"""Typed keyboard snapshots, immediate browser policies and owned focus."""

from pysx import Fragment, component, html

from .components import (
    Card,
    Description,
    Eyebrow,
    Page,
    Title,
)
from .components.events import event_controls


@component
def app() -> Fragment:
    controls = event_controls()

    return html(
        t"""
        Page(id="events-example")
            Eyebrow: "Browser capabilities"
            Title: "Keyboard & focus"
            Description: "Choose a color with Arrow Up/Down and Enter. Escape closes the list."
            Description: "Tab reaches the shortcuts; Left/Right moves focus."
            Description: "Each session has its own state."
            Card:
                {controls}
    """,
    )
