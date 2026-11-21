"""Viewport reveals and connection status declared in Python, with no application JavaScript."""

from pysx import DisconnectFor, Fragment, Reveal, browser, each, pysx

from .components import (
    Action,
    Actions,
    Card,
    Description,
    Eyebrow,
    Page,
    Title,
    example_app,
    example_title,
)

SECTIONS = (
    ("Server-owned state", "Every value on this page still lives on the server."),
    ("Browser-owned behaviour", "Entering the viewport is decided in the browser."),
    ("No round trip", "Revealing a section sends nothing over the transport."),
    ("Reduced motion", "A visitor who asks for reduced motion sees every section at once."),
)


def app() -> Fragment:
    example_title("Reveal")

    connection = browser.connection()
    reveal = Reveal(visible_class="is-visible", root_margin="0px 0px -10% 0px")
    status = connection.text(
        connected="Connected",
        reconnecting="Disconnected. Reconnecting…",
        default="Connecting…",
    )

    def section(entry: tuple[str, str]) -> Fragment:
        return pysx(t"""
            Card(class="reveal-section" behaviors={reveal}):
                h2: {entry[0]}
                Description: {entry[1]}
        """)

    return pysx(t"""
        Page:
            header:
                Eyebrow: "pysx / examples"
                Title: "Reveal on scroll"
                Description:
                    "Scroll down: each section gains its visible class as it enters the "
                    "viewport. The application declares the behaviour and writes no "
                    "observer, listener or script of its own."
            Card:
                h2: "Connection"
                p(id="reveal-status" role="status" aria-live="polite"):
                    {status}
                Actions:
                    Action(id="reveal-disconnect" type="button"
                           disabled={connection.not_connected}
                           onClick={DisconnectFor(seconds=5)}):
                        "Disconnect for 5 seconds"
                Description:
                    "The status text and the button's disabled state are browser-local "
                    "bindings, so they stay correct while the transport is down."
            {each(SECTIONS, section, key=lambda entry: entry[0])}
    """)


example = example_app(app)
