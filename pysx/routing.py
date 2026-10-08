"""Session-owned route state and independent, keyed route views."""

from __future__ import annotations

import inspect
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Unpack
from urllib.parse import parse_qsl, unquote, urlsplit

from .composition import namespace_for
from .events import BrowserEvent, EventHandler, on_event
from .native_support import element
from .reactive import Signal, batch, derived, signal
from .render import Fragment, each, pysx

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from string.templatelib import Template

    from .composition import Children
    from .native import AAttrs
    from .wire import NavigationMessage


@dataclass(frozen=True)
class Location:
    path: str
    search: str = ""
    hash: str = ""

    @property
    def url(self) -> str:

        return self.path + self.search + self.hash

    @property
    def query(self) -> tuple[tuple[str, str], ...]:

        return tuple(parse_qsl(self.search.removeprefix("?"), keep_blank_values=True))

    @classmethod
    def parse(cls, url: str) -> Location:
        if (
            not url.startswith("/")
            or url.startswith("//")
            or len(url) > 8192
            or re.search(r"[\x00-\x20\x7f\\]", url)
            or re.search(r"%(?![0-9a-fA-F]{2})", url)
        ):

            raise ValueError("location requires a bounded, local absolute URL")
        parts = urlsplit(url)
        # Validate encoding without decoding separators before matching.
        unquote(parts.path, errors="strict")
        unquote(parts.query, errors="strict")
        unquote(parts.fragment, errors="strict")

        return cls(
            parts.path,
            "?" + parts.query if parts.query else "",
            "#" + parts.fragment if parts.fragment else "",
        )


@dataclass(frozen=True)
class RouteState:
    location: Signal[Location]
    params: Signal[Mapping[str, str]]


@dataclass(frozen=True)
class Route:
    path: str
    view: Callable[[RouteState], Template | Fragment] | None = None
    children: tuple[Route, ...] = ()


@dataclass(frozen=True)
class RouteResponse:
    """Metadata for HTTP hosts; this module does not issue HTTP responses."""

    location: Location
    pattern: str | None
    status: int


def match_route(pattern: str, path: str) -> Mapping[str, str] | None:
    path = path.split("?", 1)[0].split("#", 1)[0]
    parts = path.strip("/").split("/") if path.strip("/") else []
    expected = pattern.strip("/").split("/") if pattern.strip("/") else []
    params: dict[str, str] = {}

    for index, segment in enumerate(expected):
        if segment == "*":

            return MappingProxyType(params)

        if index >= len(parts):

            return None

        if segment.startswith(":"):
            if len(segment) > 1:
                params[segment[1:]] = unquote(parts[index], errors="strict")
        elif segment != parts[index]:

            return None

    return MappingProxyType(params) if len(parts) == len(expected) else None


class RouteHost:
    def __init__(self, url: str = "/") -> None:
        self.location = Location.parse(url)
        self.routers: list[Router] = []
        self.revision = 0
        self.intercepting = False
        self.commands: list[NavigationMessage] = []

    def observe(self, url: str) -> None:
        location = Location.parse(url)
        self.location = location
        with batch():
            for router in self.routers:
                router.update(location)

    def navigate(self, url: str, *, replace: bool = False) -> None:
        external = bool(urlsplit(url).scheme or url.startswith("//"))
        # Queue this decision before effects can enqueue a redirect from its state.
        with batch():
            if not external:
                self.observe(url)
            self.commands.append(
                {
                    "t": "navigation",
                    "url": url,
                    "rev": self.revision,
                    "mode": "external" if external else ("replace" if replace else "push"),
                }
            )

    def accept_revision(self, value: object) -> bool:
        if type(value) is not int or value < self.revision:

            return False
        self.revision = value

        return True


route_host: ContextVar[RouteHost | None] = ContextVar("pysx_route_host", default=None)


class Router:
    def __init__(
        self, routes: Sequence[Route], *, initial: str | None = None, base_path: str = "/"
    ) -> None:
        base = Location.parse(base_path)

        if base.search or base.hash:

            raise ValueError("base_path cannot contain search or hash")
        self.base_path = base.path.rstrip("/")
        self._routes: list[tuple[str, Route]] = []
        self._fallback: tuple[str, Route] | None = None
        self._flatten(routes, "")
        host = route_host.get()
        self.location = signal(
            Location.parse(initial)
            if initial is not None
            else (host.location if host is not None else Location.parse("/"))
        )
        self.path = derived(lambda: self.location().path)
        self.search = derived(lambda: self.location().search)
        self.hash = derived(lambda: self.location().hash)
        self.query = derived(lambda: self.location().query)
        self.params: Signal[Mapping[str, str]] = signal({})
        self.state = RouteState(self.location, self.params)
        self._active: Signal[list[int]] = signal([])
        self.response = RouteResponse(self.location(), None, 404)
        self.update(self.location())
        self.host = host if host is not None else RouteHost(self.location().url)
        self.host.routers.append(self)

    def _flatten(self, routes: Sequence[Route], parent: str) -> None:
        for route in routes:
            pattern = route.path if route.path.startswith("/") else parent + "/" + route.path

            if route.path == "*":
                self._fallback = (pattern, route)
            elif route.view is not None:
                self._routes.append((pattern, route))
            self._flatten(route.children, pattern.rstrip("/"))

    def update(self, location: Location) -> None:
        path = location.path
        inside = (
            not self.base_path or path == self.base_path or path.startswith(self.base_path + "/")
        )
        path = path[len(self.base_path) :] or "/" if inside else path
        active: list[int] = []
        params: Mapping[str, str] = {}
        pattern: str | None = None

        if inside:
            for index, (candidate, _route) in enumerate(self._routes):
                found = match_route(candidate, path)

                if found is not None:
                    active = [index]
                    params, pattern = dict(found), candidate

                    break

            if not active and self._fallback is not None:
                active = [-1]
                pattern = self._fallback[0]
        self.response = RouteResponse(location, pattern, 200 if active else 404)
        with batch():
            self.location.set(location)
            self.params.set(params)
            self._active.set(active)

    def _view(self, index: int) -> Fragment:
        entry = self._fallback if index == -1 else self._routes[index]
        assert entry is not None
        callback = entry[1].view

        if callback is None:

            return pysx(t"")
        result = callback(self.state)

        fragment = result if isinstance(result, Fragment) else Fragment(result)

        fragment = dataclass_replace(
            fragment, namespace=fragment.scope_namespace(namespace_for(callback))
        )

        return element("div", (fragment,), {"style": "display: contents"})

    def view(self) -> Fragment:

        return pysx(t"{each(self._active, self._view, key=str)}")

    def resolve(self, destination: str) -> str:

        return resolve_navigation(self.location(), destination)

    def navigate(self, destination: str, *, replace: bool = False) -> None:
        self.host.navigate(self.resolve(destination), replace=replace)


def resolve_navigation(location: Location, destination: str) -> str:
    if not destination or len(destination) > 8192 or re.search(r"[\x00-\x20\x7f\\]", destination):

        raise ValueError("navigation requires a bounded destination without whitespace")
    parts = urlsplit(destination)

    if parts.scheme or destination.startswith("//"):
        if parts.scheme.lower() not in {"", "http", "https"} or not parts.netloc:

            raise ValueError("server navigation supports only HTTP(S) external URLs")

        return destination

    if destination.startswith("#"):
        url = location.path + location.search + destination
    elif destination.startswith("?"):
        url = location.path + destination
    elif destination.startswith("/"):
        url = destination
    else:
        url = location.path.rstrip("/") + "/" + destination
    # Normalize literal and browser-recognized encoded dot segments, clamping at root.
    accepted = Location.parse(url)
    segments: list[str] = []

    for segment in accepted.path.split("/"):
        decoded = unquote(segment).lower()

        if decoded == "..":
            if len(segments) > 1:
                segments.pop()
        elif decoded != ".":
            segments.append(segment)

    if unquote(accepted.path.rsplit("/", 1)[-1]) in {".", ".."}:
        segments.append("")

    return Location("/".join(segments) or "/", accepted.search, accepted.hash).url


@dataclass(frozen=True)
class NavigationEvent:
    event: BrowserEvent
    destination: str
    eligible: bool
    _decision: list[bool] = field(default_factory=lambda: [False], repr=False)

    def cancel_navigation(self) -> None:
        self._decision[0] = True

    @property
    def cancelled(self) -> bool:

        return self._decision[0]


def Link(  # noqa: N802 - public component constructor
    *content: object,
    router: Router,
    replace: bool = False,
    on_navigate: Callable[[NavigationEvent], object] | None = None,
    children: Children | None = None,
    **attrs: Unpack[AAttrs],
) -> Fragment:
    """An ordinary anchor with composed, cancellable session navigation."""
    href = attrs.get("href")

    if href is None:

        raise TypeError("Link requires href")
    supplied = attrs.get("on_click")
    policy = supplied if isinstance(supplied, EventHandler) else on_event(lambda _event: None)
    callback = supplied.callback if isinstance(supplied, EventHandler) else supplied

    async def clicked(event: BrowserEvent) -> None:
        before = len(router.host.commands)
        decision = NavigationEvent(
            event, event.value, router.host.intercepting and not policy.prevent_default
        )

        if callback is not None:
            result = callback(event)

            if inspect.isawaitable(result):
                await result

        if on_navigate is not None:
            result = on_navigate(decision)

            if inspect.isawaitable(result):
                await result

        if decision.eligible and not decision.cancelled and len(router.host.commands) == before:
            router.navigate(decision.destination, replace=replace)

    def resolved_href() -> str:
        value = str(href() if isinstance(href, Signal) else href)

        if not value or value.startswith("#") or urlsplit(value).scheme or value.startswith("//"):

            return value

        return router.resolve(value)

    values: dict[str, object] = dict(attrs)
    values["href"] = derived(resolved_href)
    values["on_click"] = dataclass_replace(policy, callback=clicked, navigation=True)

    if children is not None:
        content = (*content, children)

    return element("a", content, values)
