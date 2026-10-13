"""Request context retained by one application's rendered graph."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass
class Document:
    title: str = "pysx"
    description: str = ""
    status: int | None = None
    redirect: str | None = None


@dataclass(frozen=True)
class RequestContext:
    path: str = "/"
    headers: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    principal: str | None = None
    base_path: str = ""
    document: Document = field(default_factory=Document)


request_context: ContextVar[RequestContext | None] = ContextVar("pysx_request", default=None)


def current_request() -> RequestContext:
    context = request_context.get()

    if context is None:
        raise RuntimeError("request context requires an application session")

    return context


def browser_id(request: RequestContext) -> str:
    cookie = SimpleCookie()
    cookie.load(request.headers.get("cookie", ""))
    value = cookie.get("pysx_browser")

    return value.value if value is not None else ""
