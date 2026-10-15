"""Disposable durable worker for process and browser ownership tests."""

from __future__ import annotations

import asyncio
import os
from http.cookies import SimpleCookie
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
    TransportLimits,
    native,
    push,
)
from pysx.http import HTTPResult

if TYPE_CHECKING:
    from pysx.request import RequestContext
    from pysx.wire import JSONValue


def decode(value: JSONValue) -> int:
    if type(value) is not int:
        raise ValueError("expected integer")

    return value


def principal(request: RequestContext) -> str:
    cookies = SimpleCookie()
    cookies.load(request.headers.get("cookie", ""))
    user = cookies.get("fixture_user")

    return user.value if user is not None else "alice"


def app(state: Signal[int]) -> Fragment:
    generation = uuid4().hex

    def increment(_value: object) -> None:
        state.set(state() + 1)

    async def slow(_value: object) -> None:
        await asyncio.sleep(2)
        await push(lambda: state.set(state() + 10))

    return native.Main(
        native.H1("Durable counter"),
        native.Span(state, id="durable-count"),
        native.Button("Increment", id="durable-increment", on_click=increment),
        native.Button("Delayed update", id="durable-slow", on_click=slow),
        native.Span(generation, id="durable-generation"),
    )


class FixtureHost(DurableApplication[int]):
    async def http(self, request: RequestContext, *, method: str = "GET") -> HTTPResult:
        if request.path == "/_fixture/pause":
            self.renewing = False

            return HTTPResult(200, (), b"paused")

        if request.path == "/_fixture/drain":
            self.begin_drain()

            return HTTPResult(200, (), b"draining")

        if request.path == "/_fixture/revoke":
            self.revoke("alice")

            return HTTPResult(200, (), b"revoked")

        return await super().http(request, method=method)


host = FixtureHost(
    app,
    initial=lambda: 0,
    codec=StateCodec[int](lambda value: value, decode),
    store=SQLiteOwnershipStore(Path(os.environ["PYSX_FIXTURE_STORE"])),
    application="worker-counter",
    worker=os.environ["PYSX_FIXTURE_WORKER"],
    lease_seconds=float(os.environ.get("PYSX_FIXTURE_LEASE", "1")),
    schema=int(os.environ.get("PYSX_FIXTURE_SCHEMA", "1")),
    migrations={1: lambda value: value} if os.environ.get("PYSX_FIXTURE_MIGRATE") else {},
    fresh_on_incompatible=os.environ.get("PYSX_FIXTURE_FRESH") == "1",
    config=HostConfig(
        principal=principal,
        transport=TransportLimits(graphs=100, detached=50),
    ),
)
