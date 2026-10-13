"""Real failed handlers and graph failures for browser recovery verification."""

import asyncio
from typing import TYPE_CHECKING

from pysx import (
    ErrorBoundary,
    ErrorInfo,
    Fragment,
    derived,
    each,
    native,
    on_cleanup,
    on_event,
    own_effect,
    pysx,
    signal,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from string.templatelib import Template


def app() -> Fragment:
    healthy = signal(0)
    mutations = signal(0)
    trigger = signal("")
    cleanups = signal(0)
    text = signal("initial")

    def fail() -> None:
        mutations.update(lambda value: value + 1)

        raise RuntimeError("PRIVATE_EXCEPTION_TEXT")

    def bad(_: object) -> None:
        fail()

    async def async_bad(_: object) -> None:
        await asyncio.sleep(0)
        fail()

    def content() -> Fragment:
        def cleanup() -> None:
            cleanups.update(lambda value: value + 1)

            if trigger() == "cleanup":
                fail()

        on_cleanup(cleanup)

        def observe() -> None:
            if trigger() == "effect":
                fail()

        own_effect("observe", observe)

        def computed() -> str:
            if trigger() == "computed":
                fail()

            return "Ready"

        value = derived(computed)

        def render_child() -> Template:
            if trigger() == "render":
                fail()

            return t'\nspan: "Rendered"'

        shown = derived(lambda: trigger() != "cleanup")
        rendering = derived(lambda: trigger() == "render")
        key = on_event(bad, keys=("Enter",), prevent_default=True)
        select = native.Select(
            native.Option("Choose", value="a"),
            native.Option("Fail", value="b"),
            id="change-fail",
            on_change=on_event(bad),
        )
        form = native.Form(
            native.Button("Submit", id="submit-fail", type="submit"), on_submit=on_event(bad)
        )

        def row(_: str) -> Fragment:
            return native.Button("List failure", id="list-fail", on_click=bad)

        return pysx(
            t"""
            if {shown}:
                if {rendering}:
                    Child:
                else:
                    span: "Rendered"
            p(id="ready"): {value}
            button(id="fail", onClick={bad}): "Fail"
            button(id="async-fail", onClick={async_bad}): "Async failure"
            {native.Input(id="input-fail", on_input=on_event(bad))}
            {native.Input(id="key-fail", on_keydown=key)}
            {select}
            {form}
            {each(["row"], row, key=str)}
        """,
            namespace={"Child": render_child},
        )

    def fallback(info: ErrorInfo, retry: Callable[[], None]) -> Fragment:
        def reset(_: object) -> None:
            trigger.set("")
            retry()

        return native.Section(
            native.P(info.message, id="fallback"),
            native.Button("Retry", id="retry", on_click=reset),
            role="alert",
        )

    boundary = ErrorBoundary(content, fallback)

    def increment(_: object) -> None:
        healthy.update(lambda value: value + 1)

    def inject(mode: str) -> Callable[[object], None]:
        def click(_: object) -> None:
            trigger.set(mode)

        return click

    controls = native.Div(
        *(
            native.Button(mode, id=f"{mode}-fail", on_click=inject(mode))
            for mode in ("render", "computed", "effect", "cleanup")
        )
    )

    return pysx(
        t"""
        Boundary:
        {controls}
        {native.Input(id="healthy-field", bind_value=text)}
        button(id="healthy", onClick={increment}): {healthy}
        p(id="mutations"): {mutations}
        p(id="cleanups"): {cleanups}
    """,
        namespace={"Boundary": boundary},
    )
