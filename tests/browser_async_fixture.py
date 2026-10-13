"""Independent timer/resource push and a superseded cancellation-resistant load."""

import asyncio

from pysx import Fragment, derived, native, own_timer, resource, signal


def app() -> Fragment:
    count = signal(0)
    requests = 0

    async def load() -> str:
        nonlocal requests
        requests += 1
        version = requests

        try:
            await asyncio.sleep(0.3 if version == 2 else 0.05)
        except asyncio.CancelledError:
            await asyncio.sleep(0.1)

        return f"result-{version}"

    value = resource("value", load)
    own_timer("tick", 0.2, lambda: count.set(1))

    def reload(_: object) -> None:
        value.reload()

    return native.Main(
        native.P("Async resources"),
        native.P(count, id="timer"),
        native.P(derived(lambda: value().status), id="status"),
        native.P(derived(lambda: value().value or "none"), id="value"),
        native.Button("Reload", id="reload", on_click=reload),
    )
