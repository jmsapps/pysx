"""Ordinary state for browser checkpoint publication checks."""

import asyncio

from pysx import Fragment, native, own_timer, signal


def app() -> Fragment:
    count = signal(0)
    hidden = signal(0)
    draft = signal("")
    own_timer("hidden-push", 2.0, lambda: hidden.set(hidden() + 1))

    async def delayed(_event: object) -> None:
        await asyncio.sleep(0.02)
        hidden.set(hidden() + 1)

    return native.Main(
        native.P(count, id="recovery-count"),
        native.Input(bind_value=draft, id="recovery-draft"),
        native.Button("Increment", id="increment", on_click=lambda _: count.set(count() + 1)),
        native.Button("Hidden", id="hidden", on_click=lambda _: hidden.set(hidden() + 1)),
        native.Button("Delayed", id="delayed", on_click=delayed),
    )
