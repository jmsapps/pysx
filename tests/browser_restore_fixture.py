"""Restoration candidates with observable managed resource ownership."""

import asyncio
from typing import TYPE_CHECKING, cast

from pysx import (
    Fragment,
    native,
    on_cleanup,
    on_setup,
    own_effect,
    own_subscription,
    own_task,
    own_timer,
    resource,
    signal,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysx import Signal

held: dict[str, Signal[object]] = {}
events: list[str] = []
fail_restored = False
duplicate_site = False


def app(initial: object = 0) -> Fragment:
    count = signal(initial)
    draft = signal("")
    held["count"] = count
    held["draft"] = cast("Signal[object]", draft)
    on_setup(lambda: events.append("setup"))
    on_cleanup(lambda: events.append("cleanup"))
    own_effect("effect", lambda: events.append("effect"))

    def subscribe() -> Callable[[], object]:
        events.append("subscription")

        return lambda: events.append("unsubscribe")

    async def task() -> None:
        events.append("task")
        await asyncio.sleep(3600)

    async def load() -> str:
        events.append("load")

        return "loaded"

    own_subscription("subscription", subscribe)
    own_timer("timer", 3600, lambda: events.append("timer"))
    own_task("task", task)
    resource("loader", load)

    if fail_restored and count() == 13:
        raise RuntimeError("restored state cannot construct this graph")

    return native.Main(native.P(count), native.Input(bind_value=draft))


def repeated_site() -> Signal[int]:
    return signal(0)


def repeated_app() -> Fragment:
    events.append("repeated-setup")
    stable = signal("")
    first = repeated_site()
    held["stable"] = cast("Signal[object]", stable)
    held["repeat-first"] = cast("Signal[object]", first)

    if duplicate_site:
        second = repeated_site()
        held["repeat-second"] = cast("Signal[object]", second)

    return native.Main(native.P(stable), native.P(first))
