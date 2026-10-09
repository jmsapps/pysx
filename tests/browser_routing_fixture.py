"""Navigation fixture for real browser history and native click policies."""

import asyncio

from pysx import (
    Fragment,
    Link,
    NavigationEvent,
    Route,
    Router,
    RouteState,
    local_state,
    on_cleanup,
    pysx,
    signal,
)


def app() -> Fragment:
    calls = signal(0)
    cleaned = signal(0)

    def page(state: RouteState) -> Fragment:
        count = local_state("count", 0)
        on_cleanup(lambda: cleaned.set(cleaned() + 1))

        def increment(_: object) -> None:
            count.set(count() + 1)

        return pysx(t"""
            section(id="route"):
              p(id="route-path"): {state.location().path}
              p(id="param"): {state.params().get("id", "none")}
              button(id="increment", onClick={increment}): {count}
        """)

    router = Router([Route("/users/:id", page), Route("/files/*", page), Route("*", page)])
    live_href = signal("/users/2")

    def clicked(_: object) -> None:
        calls.set(calls() + 1)

    def cancel(event: NavigationEvent) -> None:
        event.cancel_navigation()

    async def delayed(_: NavigationEvent) -> None:
        await asyncio.sleep(0.03)

    def change_href(_: object) -> None:
        live_href.set("/users/3")

    def programmatic(_: object) -> None:
        router.navigate("/users/4?program=yes#part", replace=True)

    cancel_link = Link(
        "Cancel",
        router=router,
        href="/cancelled",
        id="cancel",
        on_click=clicked,
        on_navigate=cancel,
    )
    async_link = Link(
        "Async", router=router, href="/users/8", id="async", on_click=clicked, on_navigate=delayed
    )
    download_link = Link(
        "Download", router=router, href="/download", download="", id="download", on_click=clicked
    )
    external_link = Link(
        "External", router=router, href="https://example.invalid/", id="external", on_click=clicked
    )

    return pysx(t"""
        main:
          p(id="url"): {router.location}
          p(id="search"): {router.search}
          p(id="hash"): {router.hash}
          p(id="calls"): {calls}
          p(id="cleaned"): {cleaned}
          nav:
            {Link("User 1", router=router, href="/users/1?old=yes#old", id="one", on_click=clicked)}
            {Link("Live", router=router, href=live_href, id="live", on_click=clicked)}
            {Link("Descend", router=router, href="edit?mode=raw", id="descend")}
            {Link("Dot child", router=router, href="./edit", id="dot-child")}
            {Link("Sibling", router=router, href="../settings", id="sibling")}
            {Link("Parent", router=router, href="../", id="parent")}
            {Link("Grandparent", router=router, href="../../", id="grandparent")}
            {Link("Root", router=router, href="/", id="root")}
            {cancel_link}
            {async_link}
            {Link("Wildcard", router=router, href="/files/a/b/", id="wildcard")}
            {Link("Fragment", router=router, href="#native", id="fragment", on_click=clicked)}
            {download_link}
            {Link("Blank", router=router, href="/", target="_blank", id="blank", on_click=clicked)}
            {external_link}
          button(id="change-href", onClick={change_href}): "Change href"
          button(id="programmatic", onClick={programmatic}): "Replace"
          {router.view()}
          p(id="native"): "Native target"
    """)
