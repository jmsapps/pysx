"""Owned effects, a cancellable auto runner and replaceable delayed work."""

import asyncio
from typing import TYPE_CHECKING

from pysx import (
    Fragment,
    derived,
    own_effect,
    own_task,
    push,
    pysx,
    resource,
    signal,
)

from .components import Action, Card, Description, Page, Title, example_app, example_title

if TYPE_CHECKING:
    from collections.abc import Callable


def app() -> Fragment:
    example_title("Effects")
    count = signal(0)
    visible = signal(True)
    automatic = signal(False)
    history = signal("")
    entries: list[str] = []

    def log(message: str) -> None:
        entries.append(message)
        del entries[:-10]
        history.set(" | ".join(entries))

    def Observer() -> Fragment:
        def observe() -> Callable[[], None]:
            value = count()
            log(f"run {value}")

            def cleanup() -> None:
                log(f"cleanup {value}")

            return cleanup

        own_effect("counter", observe)

        return pysx(t'p(id="effect-observer"): "Observer attached"')

    async def runner() -> None:
        while True:
            await asyncio.sleep(0.7)

            def tick() -> None:
                if automatic():
                    count.update(lambda value: value + 1)

            await push(tick)

    own_task("auto-runner", runner)
    generation = 0

    async def delayed() -> str:
        nonlocal generation
        generation += 1
        token = generation
        await asyncio.sleep(1.2)

        return f"Finished {token}"

    result = resource("delayed", delayed)
    status = derived(lambda: result().value or result().status)

    def increment(_: object) -> None:
        count.update(lambda value: value + 1)

    def toggle(_: object) -> None:
        visible.set(not visible())

    def auto(_: object) -> None:
        automatic.set(not automatic())

    def trigger(_: object) -> None:
        result.reload()

    def cancel(_: object) -> None:
        result.cancel()

    return pysx(t"""
        Page(id="effects-example")
            Title: "Effects and cancellation"
            Card:
                Description: "Each change cleans up the previous effect before running again."
                Action(id="effect-increment" onClick={increment}): "Increment"
                Action(id="effect-toggle" onClick={toggle}): "Attach or dispose observer"
                p(id="effect-count"): {count}
                if {visible}:
                    Observer:
                p(id="effect-history" role="log"): {history}
            Card:
                Description:
                    "Start and stop automatic increments. Leaving the session cancels its task."
                Action(id="effect-auto" onClick={auto}): "Toggle automatic increments"
                p(id="effect-running"): {automatic}
            Card:
                Description: "Trigger twice: the second request cancels and replaces the first."
                Action(id="effect-trigger" onClick={trigger}): "Trigger delayed update"
                Action(id="effect-cancel" onClick={cancel}): "Cancel delayed update"
                p(id="effect-delayed"): {status}
    """)


example = example_app(app)
