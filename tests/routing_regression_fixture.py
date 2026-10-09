"""Fixtures for late-created routers and navigation that fails mid-handler."""

from pysx import (
    DomError,
    Fragment,
    Route,
    Router,
    RouteState,
    pysx,
    signal,
    when,
)


def late_router_app() -> Fragment:
    """No router exists until a handler reveals the component that builds one."""
    revealed = signal(False)
    routers: list[Router] = []

    def reveal(_: object) -> None:
        revealed.set(True)

    def branch() -> Fragment:
        def page(state: RouteState) -> Fragment:
            return pysx(t'p(id="late-route"): {state.location().path}')

        router = Router([Route("/", page), Route("*", page)])
        routers.append(router)

        return pysx(t"""
            section(id="late"):
              p(id="late-url"): {router.location}
              {router.view()}
        """)

    return pysx(t"""
        main:
          button(id="reveal", onClick={reveal}): "Reveal"
          {when(conditions=[(revealed, branch)], default=lambda: pysx(t'p: "quiet"'))}
    """)


def failing_navigation_app() -> Fragment:
    """A handler that commits navigation and then fails later in the same turn."""

    def page(state: RouteState) -> Fragment:
        return pysx(t'p(id="route"): {state.location().path}')

    router = Router([Route("/", page), Route("*", page)])

    def navigate_then_fail(_: object) -> None:
        router.navigate("/arrived")

        raise DomError("handler failed after navigating")

    return pysx(t"""
        main:
          p(id="url"): {router.location}
          button(id="go", onClick={navigate_then_fail}): "Go"
          {router.view()}
    """)
