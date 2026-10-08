"""Route state, lifecycle and browser navigation contracts."""

import asyncio
import re
from dataclasses import asdict
from typing import TYPE_CHECKING

import pytest

from pysx import (
    BrowserEvent,
    Fragment,
    Link,
    Location,
    NavigationEvent,
    Route,
    Router,
    RouteState,
    local_state,
    on_cleanup,
    pysx,
    signal,
)
from pysx.forms import PayloadError
from pysx.routing import match_route, resolve_navigation
from pysx.server import Session

if TYPE_CHECKING:
    from collections.abc import Mapping


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("/", "/", {}),
        ("/users", "/users/", {}),
        ("/users/", "/users", {}),
        ("/users", "/users/1", None),
        ("/users/:id", "/users/2", {"id": "2"}),
        ("/:first/:last", "/Ada/Lovelace", {"first": "Ada", "last": "Lovelace"}),
        ("/files/*", "/files", {}),
        ("/files/*", "/files/a/b", {}),
        ("*", "/anything", {}),
        ("/files/*/ignored", "/files", {}),
        ("/Users", "/users", None),
        ("/a/b", "/a//b", None),
        ("/:id", "/a%2Fb", {"id": "a/b"}),
        ("/:name", "/caf%C3%A9", {"name": "café"}),
        ("/:id", "/7?mode=raw#target", {"id": "7"}),
        ("/:/:id", "/ignored/7", {"id": "7"}),
    ],
)
def test_pysx_12_st_1_route_state_truth_table(
    pattern: str, path: str, expected: Mapping[str, str] | None
) -> None:
    assert match_route(pattern, path) == expected


@pytest.mark.parametrize(
    "url", ["//evil.test/x", "https://evil.test", "/a b", "/%zz", "/%ff", "/a\\b"]
)
def test_pysx_12_st_1_route_state_invalid_url(url: str) -> None:
    with pytest.raises(ValueError, match=r"location|decode"):
        Location.parse(url)


def test_pysx_12_st_1_route_state_nested_fallback_query_and_base() -> None:
    def page(_: RouteState) -> Fragment:

        return pysx(t'p: "page"')

    router = Router(
        [Route("users", page, (Route(":id", page), Route("/other", page))), Route("*", page)],
        initial="/app/users/7?q=one&q=two&blank=#target",
        base_path="/app",
    )
    assert router.params() == {"id": "7"}
    assert router.path() == "/app/users/7"
    assert router.search() == "?q=one&q=two&blank="
    assert router.hash() == "#target"
    assert router.query() == (("q", "one"), ("q", "two"), ("blank", ""))
    assert router.response.pattern == "/users/:id"
    router.update(Location.parse("/app/other"))
    assert router.response.pattern == "/other"
    router.update(Location.parse("/app/missing"))
    assert router.params() == {}
    assert router.response.status == 200
    router.update(Location.parse("/application/users/7"))
    assert router.response.status == 404
    assert router.params() == {}


def test_pysx_12_st_1_route_state_scope_identity_cleanup_and_isolation() -> None:
    routers: list[Router] = []
    owners: list[object] = []
    disposed: list[str] = []

    def page(state: RouteState) -> Fragment:
        count = local_state("count", 0)
        owners.append(count)
        on_cleanup(lambda: disposed.append("page"))

        return pysx(t"p: {state.params()['id']} {count}")

    def app() -> Fragment:
        router = Router([Route("/users/:id", page)])
        routers.append(router)

        return router.view()

    first = Session(app, location="/users/1")
    owner = owners[0]
    second = Session(app, location="/users/9")
    first.routes.observe("/users/2?x=1#target")
    assert owners[-1] is owner
    assert routers[0].params() == {"id": "2"}
    assert routers[1].params() == {"id": "9"}
    assert disposed == []
    assert first.pending
    first.routes.observe("/missing")
    assert disposed == ["page"]
    assert routers[0].response.status == 404
    first.routes.observe("/users/3")
    assert owners[-1] is not owner
    first.dispose()
    second.dispose()
    assert disposed == ["page", "page", "page"]
    assert routers[0].params.observers == {}


def test_pysx_12_st_1_route_state_grouping_first_match_and_last_fallback() -> None:
    def page(_: RouteState) -> Fragment:

        return pysx(t'p: "first"')

    def fallback(_: RouteState) -> Fragment:

        return pysx(t'p: "last fallback"')

    router = Router(
        [
            Route("users", children=(Route(":id", page),)),
            Route("/users/*", fallback),
            Route("*", page),
            Route("*", fallback),
        ],
        initial="/users/1",
    )
    assert router.response.pattern == "/users/:id"
    router.update(Location.parse("/missing"))
    session = Session(router.view)
    assert "last fallback" in session.rendered.body
    session.dispose()


@pytest.mark.parametrize(
    ("destination", "expected"),
    [
        ("/", "/"),
        ("settings", "/relative/users/1/settings"),
        ("./settings", "/relative/users/1/settings"),
        ("/settings", "/settings"),
        ("edit?mode=raw", "/relative/users/1/edit?mode=raw"),
        ("./edit?mode=raw#part", "/relative/users/1/edit?mode=raw#part"),
        ("../settings", "/relative/users/settings"),
        ("../", "/relative/users/"),
        ("../../", "/relative/"),
        ("../../../", "/"),
        ("../../../../", "/"),
        ("../../../../settings", "/settings"),
        (".", "/relative/users/1/"),
        ("..", "/relative/users/"),
        ("child/../other/", "/relative/users/1/other/"),
        ("%2e%2e/settings", "/relative/users/settings"),
        ("+/edit", "/relative/users/1/+/edit"),
        ("-/settings", "/relative/users/1/-/settings"),
        ("#target", "/relative/users/1?old=yes#target"),
        ("?new=yes", "/relative/users/1?new=yes"),
        ("https://example.test/path", "https://example.test/path"),
        ("//example.test/path", "//example.test/path"),
        ("/a/%2e%2e/b?new=yes", "/b?new=yes"),
    ],
)
def test_pysx_12_st_2_navigation_native_resolution(destination: str, expected: str) -> None:
    assert (
        resolve_navigation(Location.parse("/relative/users/1?old=yes#old"), destination) == expected
    )


@pytest.mark.parametrize("path", ["/", "/users/1", "/users/1/"])
@pytest.mark.parametrize("destination", ["foo", "./foo"])
def test_pysx_12_st_2_navigation_native_directory_children(path: str, destination: str) -> None:
    assert resolve_navigation(Location.parse(path + "?old=yes#old"), destination) == (
        path.rstrip("/") + "/foo"
    )


@pytest.mark.parametrize("destination", ["../", "../../", "../../../"])
def test_pysx_12_st_2_navigation_native_root_parent(destination: str) -> None:
    assert resolve_navigation(Location.parse("/?old=yes#old"), destination) == "/"


@pytest.mark.parametrize("destination", ["", "javascript:alert(1)", "mailto:a@b", "/bad%zz", " /a"])
def test_pysx_12_st_2_navigation_native_invalid_destination(destination: str) -> None:
    with pytest.raises(ValueError, match=r"navigation|location"):
        resolve_navigation(Location.parse("/"), destination)


def test_pysx_12_st_2_navigation_native_async_composition_cancel_and_schema() -> None:
    routers: list[Router] = []
    calls: list[str] = []
    href = signal("/next")
    cancel = True

    async def clicked(_: object) -> None:
        await asyncio.sleep(0)
        calls.append("click")

    async def decide(event: NavigationEvent) -> None:
        calls.append("decision")

        if cancel:
            event.cancel_navigation()

    def app() -> Fragment:
        router = Router([])
        routers.append(router)

        return Link(
            "Next",
            router=router,
            href=href,
            on_click=clicked,
            on_navigate=decide,
            hreflang="en",
            referrerpolicy="no-referrer",
            target="_self",
            custom_attrs={"aria-label": "Next page"},
        )

    session = Session(app)
    match = re.search(r'data-pysx-click="([^"]+)"', session.rendered.body)
    assert match is not None
    handler = match[1]
    event = asdict(BrowserEvent("click", handler, value="/next"))
    assert 'hreflang="en"' in session.rendered.body
    assert 'referrerpolicy="no-referrer"' in session.rendered.body
    assert 'aria-label="Next page"' in session.rendered.body
    asyncio.run(session.dispatch_async(handler, None, event=event, navigation=True))
    assert calls == ["click", "decision"]
    assert routers[0].location().url == "/"
    assert session.routes.commands == []
    cancel = False
    asyncio.run(session.dispatch_async(handler, None, event=event, navigation=True))
    assert calls == ["click", "decision", "click", "decision"]
    assert routers[0].location().url == "/next"
    assert session.routes.commands[-1]["mode"] == "push"
    assert session.routes.accept_revision(2)
    assert not session.routes.accept_revision(1)
    assert not session.routes.accept_revision(True)
    with pytest.raises(PayloadError, match="event snapshot"):
        asyncio.run(session.dispatch_async(handler, None, event=None, navigation=True))
    assert not session.routes.intercepting
    session.dispose()


def test_pysx_12_st_3_fragment_scrolling_commit_and_response_metadata() -> None:
    from browser_fragments_fixture import app

    session = Session(app)
    router = session.routes.routers[0]
    router.navigate("/delayed#caf%C3%A9")
    assert any(op["op"] == "list" for op in session.pending)
    assert all("Decoded target" not in str(op) for op in session.pending)
    assert router.response.status == 200
    assert router.response.location.hash == "#caf%C3%A9"
    assert session.routes.commands[-1]["url"] == "/delayed#caf%C3%A9"
    session.pending.clear()
    session.rendered.scopes.acknowledge(session.rendered.scopes.pending_mounts())
    assert any("Decoded target" in str(op) for op in session.pending)
    router.navigate("/last#missing", replace=True)
    assert session.routes.commands[-1]["mode"] == "replace"
    assert router.response.location.path == "/last"
    assert session.routes.accept_revision(5)
    assert not session.routes.accept_revision(4)
    session.dispose()
