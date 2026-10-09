"""Late mount targets and history restoration over the current host."""

import asyncio

from pysx import (
    Fragment,
    Link,
    NavigationEvent,
    Route,
    Router,
    RouteState,
    local_state,
    on_mount,
    pysx,
)


def app() -> Fragment:
    def page(state: RouteState) -> Fragment:
        path = state.location().path
        ready = local_state("ready", path != "/delayed")

        if path == "/delayed":
            on_mount(lambda: ready.set(True))

        return pysx(t"""
            section:
              h1(id="route-path"): {path}
              input(id="restore-focus", aria-label="Focus for history"):
              div(style="height: 1800px"):
              if {ready}:
                h2(id="café"): "Decoded target"
                a(name="named"): "Named target"
              div(style="height: 800px"):
        """)

    router = Router([Route(path, page) for path in ["/first", "/second", "/delayed", "/last", "*"]])

    async def slow(_: NavigationEvent) -> None:
        await asyncio.sleep(0.08)

    delayed = Link("Delayed", router=router, href="/delayed#caf%C3%A9", id="delayed")
    race = Link("Slow", router=router, href="/delayed#caf%C3%A9", id="slow", on_navigate=slow)

    return pysx(t"""
        main(id="navigation-root"):
          nav:
            {Link("First", router=router, href="/first", id="first")}
            {Link("Second", router=router, href="/second", id="second")}
            {Link("Fragment", router=router, href="#caf%C3%A9", id="fragment")}
            {Link("Named", router=router, href="#named", id="named-link")}
            {Link("Missing", router=router, href="/last#unavailable", id="missing")}
            {Link("Same missing", router=router, href="#absent", id="same-missing")}
            {Link("Last", router=router, href="/last", id="last")}
            {delayed}
            {race}
          {router.view()}
    """)
