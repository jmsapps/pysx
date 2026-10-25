"""Bounded disconnect and reconnect with current-state reconciliation."""

import asyncio
from itertools import count

from pysx import Fragment, own_task, push, pysx, signal

from .components import (
    Action,
    Actions,
    Card,
    Description,
    Eyebrow,
    Field,
    Metric,
    Page,
    Title,
    Value,
    example_app,
    example_title,
)

sessions = count(1)


def app() -> Fragment:
    example_title("Reconnect")

    generation = next(sessions)
    clicks = signal(0)
    ticks = signal(0)
    draft = signal("Server draft")

    async def advance() -> None:
        while True:
            await asyncio.sleep(0.25)
            await push(lambda: ticks.update(lambda value: value + 1))

    own_task("ticks", advance)

    def increment(_: object) -> None:
        clicks.update(lambda value: value + 1)

    return pysx(t"""
        Page:
            header:
                Eyebrow: "pysx / examples"
                Title: "Bounded reconnect"
                Description:
                    "Interrupt the connection: the page reconnects by itself and adopts "
                    "the state the server holds now."
            Card:
                h2: "This session"
                p:
                    "Session number: "
                    span(id="reconnect-generation"): {generation}
                Description:
                    "The number stays the same across a reconnect and changes only when "
                    "recovery starts a fresh session."
            Card:
                h2: "Server clock"
                Metric:
                    Value(id="reconnect-ticks"): {ticks}
                    " ticks"
                Description:
                    "The server keeps counting while the browser is away. Reconnecting "
                    "shows the current total, never a replay of the ticks you missed."
            Card:
                h2: "Your state"
                label(htmlFor="reconnect-field"): "Draft text"
                Field(id="reconnect-field", bindValue={draft})
                p(id="reconnect-echo"): {draft}
                p:
                    "Clicks: "
                    span(id="reconnect-clicks"): {clicks}
                Actions:
                    Action(id="reconnect-increment", type="button", onClick={increment}):
                        "Increment"
                Description:
                    "Clicks and the draft survive a reconnect. While the page is "
                    "disconnected it refuses edits instead of queuing them."
    """)


example = example_app(app)
