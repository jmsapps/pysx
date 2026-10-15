"""Fixed recovery workload: keyed forms, routes, themes and owned async work."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field, replace
from http.cookies import SimpleCookie
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pysx import (
    DurableApplication,
    Fragment,
    HostConfig,
    Route,
    Router,
    RouteState,
    Signal,
    SQLiteOwnershipStore,
    StateCodec,
    Themes,
    TransportLimits,
    derived,
    each,
    native,
    own_effect,
    project,
    pysx,
    resource,
)

if TYPE_CHECKING:
    from pysx.request import RequestContext
    from pysx.wire import JSONValue


def principal(request: RequestContext) -> str:
    cookies = SimpleCookie()
    cookies.load(request.headers.get("cookie", ""))
    user = cookies.get("fixture_user")

    return user.value if user is not None else "alice"


@dataclass(frozen=True)
class WorkloadState:
    count: int = 0
    rows: tuple[str, ...] = field(default_factory=lambda: tuple(f"Row {i}" for i in range(100)))
    order: tuple[int, ...] = field(default_factory=lambda: tuple(range(100)))
    theme: str = "light"
    location: str = ""


def encode(state: WorkloadState) -> JSONValue:
    rows: list[JSONValue] = list(state.rows)
    order: list[JSONValue] = list(state.order)

    return {
        "count": state.count,
        "rows": rows,
        "order": order,
        "theme": state.theme,
        "location": state.location,
    }


def decode(value: JSONValue) -> WorkloadState:
    if not isinstance(value, dict):
        raise ValueError("expected workload object")
    count, rows, order = value.get("count"), value.get("rows"), value.get("order")
    theme, location = value.get("theme"), value.get("location")

    if type(count) is not int or not isinstance(rows, list) or not isinstance(order, list):
        raise ValueError("invalid workload counters or rows")

    if len(rows) != 100 or len(order) != 100:
        raise ValueError("workload requires 100 rows")
    texts: list[str] = []
    keys: list[int] = []

    for text in rows:
        if not isinstance(text, str) or len(text) > 128:
            raise ValueError("invalid row value")
        texts.append(text)

    for key in order:
        if type(key) is not int:
            raise ValueError("invalid row key")
        keys.append(key)

    if set(keys) != set(range(100)):
        raise ValueError("invalid row permutation")

    if not isinstance(theme, str) or theme not in ("light", "dark"):
        raise ValueError("invalid workload theme")

    if not isinstance(location, str) or (location and not location.startswith("/")):
        raise ValueError("invalid workload location")

    return WorkloadState(count, tuple(texts), tuple(keys), theme, location)


def app(state: Signal[WorkloadState]) -> Fragment:
    generation = uuid4().hex
    themes = Themes()
    themes.register("light", {"ink": "#123456"})
    themes.register("dark", {"ink": "#abcdef"})
    own_effect("theme", lambda: themes.select(state().theme))

    async def load() -> str:
        await asyncio.sleep(0.02)

        return "ready"

    loading = resource("workload", load)
    status = derived(lambda: loading().status)
    count = derived(lambda: state().count)
    order = derived(lambda: state().order)

    def row(index: int) -> Fragment:
        value = project(
            state,
            lambda data: data.rows[index],
            lambda data, text: replace(
                data, rows=(*data.rows[:index], text[:128], *data.rows[index + 1 :])
            ),
        )

        return native.Li(
            native.Input(bind_value=value, id=f"workload-field-{index}"),
            id=f"workload-row-{index}",
        )

    def increment(_value: object) -> None:
        data = state()
        state.set(replace(data, count=data.count + 1))

    def reorder(_value: object) -> None:
        data = state()
        state.set(
            replace(
                data,
                order=tuple(reversed(data.order)),
                theme="dark",
                location="/workload/reordered",
            )
        )
        router.navigate("/workload/reordered")

    def page(route: RouteState) -> Fragment:
        return native.Main(
            native.H1("Durable counter"),
            native.Span(count, id="durable-count"),
            native.Button("Increment", id="durable-increment", on_click=increment),
            native.Button("Reorder", id="durable-reorder", on_click=reorder),
            native.Span(generation, id="durable-generation"),
            native.Span(status, id="workload-resource"),
            native.Span(derived(lambda: route.location().path), id="workload-path"),
            native.Ul(each(order, row, key=str), id="workload-rows"),
            css="color: var(--ink)",
        )

    router = Router([Route("/workload/:view", page), Route("*", page)])
    data = state()

    if data.location and data.location != router.location().url:
        router.navigate(data.location, replace=True)
    elif not data.location:
        state.set(replace(data, location=router.location().url))

    return pysx(t"{router.view()}", themes=themes)


host = DurableApplication(
    app,
    initial=WorkloadState,
    codec=StateCodec(encode, decode),
    store=SQLiteOwnershipStore(Path(os.environ["PYSX_FIXTURE_STORE"])),
    application="worker-counter",
    worker=os.environ["PYSX_FIXTURE_WORKER"],
    lease_seconds=float(os.environ.get("PYSX_FIXTURE_LEASE", "3")),
    schema=int(os.environ.get("PYSX_FIXTURE_SCHEMA", "1")),
    migrations={1: lambda value: value} if os.environ.get("PYSX_FIXTURE_MIGRATE") else {},
    fresh_on_incompatible=os.environ.get("PYSX_FIXTURE_FRESH") == "1",
    config=HostConfig(principal=principal, transport=TransportLimits(graphs=100, detached=50)),
)
