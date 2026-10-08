"""Session-owned routes, relative links, cancellable navigation and history."""

from pysx import (
    Fragment,
    NavigationEvent,
    Route,
    Router,
    RouteState,
    derived,
    effect,
    local_state,
    on_cleanup,
    pysx,
    signal,
)

from .components.controls import Action, Actions, Description, Eyebrow, Reading, Title
from .components.navigation import ChapterGap, NavigationLink, NavigationPage, RouteCard


def app() -> Fragment:
    notice = signal("Choose a destination below.")
    cleanups = signal(0)
    needs_login = signal(False)

    def home(_: RouteState) -> Fragment:

        return pysx(t"""
            RouteCard:
              h2(id="route-title"): "Home"
              Description: "Open a user, try the relative links, then use browser Back and Forward."
        """)

    def user(state: RouteState) -> Fragment:
        count = local_state("count", 0)
        on_cleanup(lambda: cleanups.update(lambda value: value + 1))

        def increment(_: object) -> None:
            count.update(lambda value: value + 1)

        return pysx(t"""
            RouteCard:
              h2(id="route-title"): "User " {state.params()["id"]}
              p(id="user-id"): {state.params()["id"]}
              Description: "The counter stays with this route when its user parameter changes."
              Action(id="increment", onClick={increment}): "Count: " {count}
              input(id="history-focus", aria-label="Focus to restore with history"):
            ChapterGap:
            RouteCard(id="details"):
              h2: "User details"
              Description: "Fragment links bring this section into view and focus it."
            ChapterGap:
        """)

    def activity(state: RouteState) -> Fragment:

        return pysx(t"""
            RouteCard:
              h2(id="route-title"): "Activity for user " {state.params()["id"]}
              Description: "Nested activity shares the user parameter. Parent returns to the user."
        """)

    def files(state: RouteState) -> Fragment:

        return pysx(t"""
            RouteCard:
              h2(id="route-title"): "Files"
              Reading: {state.location().path}
              Description: "The wildcard matches every remaining segment."
        """)

    def missing(_: RouteState) -> Fragment:

        return pysx(t"""
            RouteCard:
              h2(id="route-title"): "Not found"
              Description: "This is the fallback route. Use Home to return."
        """)

    def login(_: RouteState) -> Fragment:

        return pysx(t"""
            RouteCard:
              h2(id="route-title"): "Sign in"
              Description: "The state change brought you here. Continue restores browsing."
              Action(id="login-continue", onClick={sign_in}): "Continue"
        """)

    router = Router(
        [
            Route("/", home),
            Route("/users", home, (Route(":id", user, (Route("activity", activity),)),)),
            Route("/files/*", files),
            Route("/login", login),
            Route("*", missing),
        ]
    )
    url = derived(lambda: router.location().url)
    redirect_status = derived(lambda: "Sign-in required" if needs_login() else "Browsing freely")

    def redirect_to_login() -> None:
        if needs_login() and router.path() != "/login":
            router.navigate("/login", replace=True)

    on_cleanup(effect(redirect_to_login).dispose)

    def require_login(_: object) -> None:
        needs_login.set(True)

    def sign_in(_: object) -> None:
        needs_login.set(False)
        router.navigate("/")

    def cancel(event: NavigationEvent) -> None:
        event.cancel_navigation()
        notice.set("Navigation cancelled. Your current route stays open.")

    return pysx(t"""
        NavigationPage:
          main(id="navigation-example"):
            Eyebrow: "Routes & history"
            Title: "A place for every view"
            Description: "Explore users, nested activity and files without leaving this session."
            Reading(id="current-url"): {url}
            p: "Query: " {router.search}
            p: "Fragment: " {router.hash}
            Actions:
              NavigationLink(router={router}, href="/", id="home"): "Home"
              NavigationLink(router={router}, href="/users/1", id="user-one"): "User 1"
              NavigationLink(router={router}, href="/users/2?tab=activity", id="user-two"): "User 2"
              NavigationLink(router={router}, href="/files/docs/start", id="files"): "Files"
            Actions:
              NavigationLink(router={router}, href="./activity", id="descend"):
                "Activity (./activity)"
              NavigationLink(router={router}, href="../3", id="sibling"): "User 3 (../3)"
              NavigationLink(router={router}, href="../", id="parent"): "Parent (../)"
              NavigationLink(router={router}, href="?tab=settings", replace={True}, id="query"):
                "Replace query"
              NavigationLink(router={router}, href="#details", id="fragment"): "Jump to details"
              NavigationLink(router={router}, href="/blocked", on_navigate={cancel}, id="cancel"):
                "Cancel navigation"
            Description: "Require sign-in to trigger an automatic redirect."
            Actions:
              Action(id="require-login", onClick={require_login}): "Require sign-in"
              Reading(id="redirect-status"): {redirect_status}
            p(id="notice"): {notice}
            p(id="cleanups"): "Routes closed: " {cleanups}
            {router.view()}
    """)
