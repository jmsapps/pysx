"""Owned async work, real failure boundaries and ordinary recoverable drafts."""

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

from pysx import (
    ErrorBoundary,
    ErrorInfo,
    Fragment,
    derived,
    local_state,
    on_mount,
    on_setup,
    own_task,
    push,
    pysx,
    resource,
    signal,
)
from src.components import Actions, Button, Field, Panel, Readout

from .foundations import counter

if TYPE_CHECKING:
    from collections.abc import Callable


def async_resource(*, server_rendering: bool = False) -> Fragment:
    draft = signal("")
    setups = signal(0)
    mounts = signal(0)
    generation = 0
    failing = signal(False)
    on_setup(lambda: setups.update(lambda value: value + 1))
    on_mount(lambda: mounts.update(lambda value: value + 1))

    async def load() -> str:
        nonlocal generation
        generation += 1
        current = generation
        should_fail = await push(failing)
        await asyncio.sleep(0.4)

        if should_fail:
            raise ValueError("intentional lesson resource failure")

        return f"Result {current}"

    result = resource("lesson-resource", load)
    status = derived(lambda: result().status)
    message = derived(lambda: result().value or "No completed result")

    def reload(_: object) -> None:
        failing.set(False)
        result.reload()

    def cancel(_: object) -> None:
        result.cancel()

    def fail(_: object) -> None:
        failing.set(True)
        result.reload()

    return pysx(t"""
        label(for="resource-draft"): "Draft stays editable during work"
        Field(id="resource-draft" bindValue={draft})
        Actions:
            Button(id="resource-reload" onClick={reload}): "Reload / retry"
            Button(id="resource-cancel" onClick={cancel}): "Cancel"
            Button(id="resource-fail" onClick={fail}): "Fail next request"
        Readout(id="resource-status" role="status"): {status}
        Readout(id="resource-message"): {message}
        p: "Server setup: "
        Readout(id="resource-setups"): {setups}
        p: "Browser mount: "
        Readout(id="resource-mounts"): {mounts}
        if {server_rendering}:
            p: "This content was included in the first HTTP response."
    """)


def errors() -> Fragment:
    sibling = signal(0)

    def content() -> Fragment:
        count = local_state("count", 0)

        def fail(_: object) -> None:
            raise ValueError("intentional lesson boundary failure")

        return pysx(t"""
            Panel(id="boundary-content")
                p: "Healthy local panel"
                Readout(id="boundary-count"): {count}
                Button(id="boundary-fail" onClick={fail}): "Fail this panel"
        """)

    def fallback(info: ErrorInfo, retry: Callable[[], None]) -> Fragment:
        def reset(_: object) -> None:
            retry()

        return pysx(t"""
            Panel(id="boundary-fallback" role="alert")
                p: {info.message}
                Button(id="boundary-retry" onClick={reset}): "Retry panel"
        """)

    Boundary = ErrorBoundary(content, fallback)

    def increment(_: object) -> None:
        sibling.update(lambda value: value + 1)

    return pysx(t"""
        Boundary:
        Button(id="sibling-increment" onClick={increment}): "Increment healthy sibling"
        Readout(id="sibling-count"): {sibling}
    """)


def sessions() -> Fragment:
    draft = signal("")
    first = counter()
    second = counter("second-")

    return pysx(t"""
        label(for="private-draft"): "Private session draft"
        Field(id="private-draft" bindValue={draft})
        Readout(id="private-echo"): {draft}
        {first}
        {second}
        p: "Open this lesson in a new tab to construct another independent graph."
    """)


def recovery(*, clock: bool = False) -> Fragment:
    draft = signal("")
    count = signal(0)
    ticks = signal(0)
    generation = uuid4().hex[:10]

    async def run() -> None:
        while True:
            await asyncio.sleep(0.5)
            await push(lambda: ticks.update(lambda value: value + 1))

    if clock:
        own_task("lesson-clock", run)

    def increment(_: object) -> None:
        count.update(lambda value: value + 1)

    return pysx(t"""
        label(for="recovery-draft"): "Delivered draft"
        Field(id="recovery-draft" bindValue={draft})
        Readout(id="recovery-echo"): {draft}
        Button(id="recovery-increment" onClick={increment}): "Increment delivered count"
        Readout(id="recovery-count"): {count}
        Readout(id="recovery-ticks"): {ticks}
        p(id="recovery-setup"): {generation}
        p: "Use the real connection status indicator. Keep this document open during interruption."
    """)
