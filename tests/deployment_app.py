"""Disposable production-configured application for Linux topology acceptance."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pysx import (
    DurableApplication,
    HostConfig,
    SecurityPolicy,
    Signal,
    SQLiteOwnershipStore,
    StateCodec,
    derived,
    native,
    push,
    resource,
)

if TYPE_CHECKING:
    from pysx import Fragment
    from pysx.runtime.wire import JSONValue


def decode(value: JSONValue) -> int:
    if type(value) is not int:
        raise ValueError("expected integer")

    return value


def app(state: Signal[int]) -> Fragment:
    async def load() -> str:
        await asyncio.sleep(0.02)

        return "ready"

    loaded = resource("startup", load)

    def increment(_event: object) -> None:
        state.set(state() + 1)

    async def delayed(_event: object) -> None:
        await asyncio.sleep(0.05)
        await push(lambda: state.set(state() + 10))

    return native.Main(
        native.H1("Linux durable deployment"),
        native.Span(state, id="count"),
        native.Span(uuid4().hex, id="generation"),
        native.Span(derived(lambda: loaded().status), id="resource"),
        native.Button("Increment", id="increment", on_click=increment),
        native.Button("Async", id="delayed", on_click=delayed),
    )


host = DurableApplication(
    app,
    initial=lambda: 0,
    codec=StateCodec[int](lambda value: value, decode),
    store=SQLiteOwnershipStore(Path(os.environ["PYSX_STATE_PATH"])),
    application="linux-acceptance",
    worker=os.environ["PYSX_WORKER"],
    config=HostConfig(
        production=True,
        secure_cookies=True,
        principal=lambda _request: "acceptance-user",
        security=SecurityPolicy(
            origins=(os.environ["PYSX_ORIGIN"],),
            trusted_proxies=("172.16.0.0/12",),
            require_principal=True,
            authorize=lambda _request, _action, _target: True,
        ),
    ),
)
