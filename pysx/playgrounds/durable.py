"""Declared session state that is reconstructed through a fresh application graph.

Scaffold this playground, then run it:

    pysx --playground durable
    pysx --app playgrounds.durable
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pysx import (
    DurableApplication,
    Fragment,
    HostConfig,
    Signal,
    SQLiteOwnershipStore,
    StateCodec,
    derived,
    project,
    pysx,
)

if TYPE_CHECKING:
    from pysx.runtime.wire import JSONValue

STORE = Path(__file__).parent / ".state" / "durable" / "store.sqlite3"


@dataclass(frozen=True)
class State:
    count: int = 0
    draft: str = "My saved draft"


def encode(state: State) -> JSONValue:
    return {"count": state.count, "draft": state.draft}


def decode(value: JSONValue) -> State:
    if not isinstance(value, dict):
        raise ValueError("expected state object")
    count, draft = value.get("count"), value.get("draft")

    if type(count) is not int or not isinstance(draft, str) or len(draft) > 512:
        raise ValueError("invalid counter or draft")

    return State(count, draft)


def view(state: Signal[State]) -> Fragment:
    setup = uuid4().hex[:12]
    count = derived(lambda: state().count)
    draft = project(
        state, lambda value: value.draft, lambda value, text: replace(value, draft=text[:512])
    )

    def increment(_event: object) -> None:
        value = state()
        state.set(replace(value, count=value.count + 1))

    return pysx(t"""
        main:
            h1: "Durable declared state"
            p: "Only declared state is persisted. Handlers and resources are rebuilt."
            label(htmlFor="draft"): "Saved draft"
            input(id="draft" bindValue={draft})
            p:
                "Counter: "
                span(id="count"): {count}
            button(id="increment" type="button" onClick={increment}): "Increment"
            p:
                "Setup marker: "
                span(id="setup"): {setup}
            h2: "Try it"
            ol:
                li: "Edit the draft and increment the counter."
                li: "Stop the server with Ctrl+C, leaving this tab open."
                li: "Start the same command again. The draft and counter reconstruct."
                li: "The setup marker changes, because the application graph is new."
            p: "This local demonstration identity is not production authentication."
    """)


STORE.parent.mkdir(parents=True, exist_ok=True)

app = DurableApplication(
    view,
    initial=State,
    codec=StateCodec[State](encode, decode),
    store=SQLiteOwnershipStore(STORE),
    application="playground-durable",
    config=HostConfig(principal=lambda _request: "local-playground"),
)
