"""Request context retained by one application's rendered graph."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


@dataclass
class Document:
    title: str = "pysx"
    description: str = ""
    status: int | None = None
    redirect: str | None = None


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """Current principal and login-session generation supplied by trusted authentication."""

    principal: str
    session_id: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.principal) is not str
            or type(self.session_id) is not str
            or not self.principal
            or not self.session_id
            or len(self.session_id) > 4096
        ):
            raise ValueError("invalid authenticated identity")


@dataclass(frozen=True)
class RequestContext:
    path: str = "/"
    headers: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    principal: str | None = None
    base_path: str = ""
    document: Document = field(default_factory=Document)
    scheme: str = "http"
    peer: str | None = None
    access: Callable[[str, str], None] | None = field(default=None, repr=False, compare=False)
    request_id: str = field(default_factory=lambda: uuid4().hex)
    authentication_session: str | None = field(default=None, repr=False)


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
