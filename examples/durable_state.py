"""Explicit typed session state, reconstructed through a fresh application graph."""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pysx import (
    DurableApplication,
    Fragment,
    HostConfig,
    SecurityPolicy,
    Signal,
    SQLiteOwnershipStore,
    StateCodec,
    current_request,
    derived,
    project,
    pysx,
    resource,
)

from .components import Action, Actions, Card, Description, Eyebrow, Field, Page, Title, Value

if TYPE_CHECKING:
    from pysx.wire import JSONValue


@dataclass(frozen=True)
class ExampleState:
    count: int = 0
    draft: str = "My saved draft"


def encode(state: ExampleState) -> JSONValue:
    return {"count": state.count, "draft": state.draft}


def decode(value: JSONValue) -> ExampleState:
    if not isinstance(value, dict):
        raise ValueError("expected state object")
    count, draft = value.get("count"), value.get("draft")

    if type(count) is not int or not isinstance(draft, str) or len(draft) > 512:
        raise ValueError("invalid counter or draft")

    return ExampleState(count, draft)


CODEC = StateCodec[ExampleState](encode, decode)


def view(state: Signal[ExampleState]) -> Fragment:
    current_request().document.title = "Durable state"
    setup = uuid4().hex[:12]
    count = derived(lambda: state().count)
    draft = project(
        state, lambda value: value.draft, lambda value, text: replace(value, draft=text[:512])
    )

    async def load() -> str:
        await asyncio.sleep(0.03)

        return "Resource rebuilt"

    loaded = resource("startup", load)
    status = derived(lambda: loaded().value or "Loading resource")

    def increment(_event: object) -> None:
        value = state()
        state.set(replace(value, count=value.count + 1))

    return pysx(t"""
        Page:
            header:
                Eyebrow: "pysx / examples"
                Title: "Durable state"
                Description:
                    "Keep this page open while restarting a worker. "
                    "Your count and draft recover."
            Card:
                h2: "Declared session state"
                label(htmlFor="durable-draft"): "Saved draft"
                Field(id="durable-draft", bindValue={draft})
                p(id="durable-echo"): {draft}
                p:
                    "Counter: "
                    Value(id="durable-count"): {count}
                Actions:
                    Action(id="durable-increment", type="button", onClick={increment}): "Increment"
            Card:
                h2: "Fresh application setup"
                p:
                    "Setup marker: "
                    span(id="durable-generation"): {setup}
                p(id="durable-resource", role="status"): {status}
                Description:
                    "Recovery preserves only declared state and rebuilds handlers and resources. "
                    "The setup marker changes after reconstruction. "
                    "Reloading this page starts a new session."
                Description: "This local demonstration identity is not production authentication."
    """)


def create_application(
    path: Path,
    *,
    worker: str | None = None,
    origin: str | None = None,
) -> DurableApplication[ExampleState]:
    return DurableApplication(
        view,
        initial=ExampleState,
        codec=CODEC,
        store=SQLiteOwnershipStore(path),
        application="durable-example",
        worker=worker,
        config=HostConfig(
            principal=lambda _request: "local-demo",
            secure_cookies=origin is not None,
            security=SecurityPolicy(
                origins=(origin,) if origin else (),
                trusted_proxies=("172.16.0.0/12",) if origin else (),
            ),
        ),
    )


app = create_application(
    Path(
        os.environ.get(
            "PYSX_STATE_PATH", str(Path(tempfile.gettempdir()) / "pysx-durable-example.sqlite3")
        )
    ),
    worker=os.environ.get("PYSX_WORKER"),
    origin=os.environ.get("PYSX_ORIGIN"),
)
