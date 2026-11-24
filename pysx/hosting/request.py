"""Request context retained by one application's rendered graph."""

import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

RESERVED_COOKIES = frozenset({"pysx_browser"})
MAX_COOKIE_NAME = 128
MAX_COOKIE_VALUE = 4096
SameSite = Literal["Lax", "Strict", "None"]
_COOKIE_NAME = re.compile(r"[A-Za-z0-9!#$%&\'*+\-.^_`|~]+")
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*")


class CookieError(ValueError):
    """A cookie could not be expressed safely in a response header."""


@dataclass(frozen=True)
class Cookie:
    """One `Set-Cookie` an application asked this response to carry."""

    name: str
    value: str
    path: str = "/"
    max_age: int | None = None
    secure: bool | None = None
    samesite: SameSite = "Lax"
    http_only: bool = False

    def __post_init__(self) -> None:
        if self.name in RESERVED_COOKIES:
            raise CookieError(f"{self.name} is reserved by the framework")

        if len(self.name) > MAX_COOKIE_NAME or _COOKIE_NAME.fullmatch(self.name) is None:
            raise CookieError("cookie name must be a non-empty token")

        if len(self.value) > MAX_COOKIE_VALUE or _COOKIE_VALUE.fullmatch(self.value) is None:
            raise CookieError("cookie value carries characters a header cannot hold")

        if not self.path.startswith("/") or any(c in self.path for c in ";,\r\n \t"):
            raise CookieError("cookie path must be an absolute path without separators")

        if self.max_age is not None and (
            type(self.max_age) is not int or not -1 <= self.max_age <= 34560000
        ):
            raise CookieError("max_age must be an integer number of seconds")

        if self.samesite not in ("Lax", "Strict", "None"):
            raise CookieError("samesite must be Lax, Strict or None")

        if self.samesite == "None" and self.secure is False:
            raise CookieError("SameSite=None requires Secure")

    def header(self, *, secure_default: bool) -> str:
        """Render this cookie, resolving Secure from the request scheme when unset."""

        secure = self.secure if self.secure is not None else secure_default
        parts = [f"{self.name}={self.value}", f"Path={self.path}", f"SameSite={self.samesite}"]

        if self.max_age is not None:
            parts.append(f"Max-Age={self.max_age}")

        if self.http_only:
            parts.append("HttpOnly")

        if secure or self.samesite == "None":
            parts.append("Secure")

        return "; ".join(parts)


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
    response_cookies: list[Cookie] = field(
        default_factory=list["Cookie"], repr=False, compare=False
    )

    @property
    def cookies(self) -> Mapping[str, str]:
        """Cookies this request carried, parsed. HttpOnly cookies appear here."""

        jar = SimpleCookie()
        jar.load(self.headers.get("cookie", ""))

        return MappingProxyType({name: morsel.value for name, morsel in jar.items()})


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


def set_cookie(
    name: str,
    value: str,
    *,
    path: str = "/",
    max_age: int | None = None,
    secure: bool | None = None,
    samesite: SameSite = "Lax",
    http_only: bool = False,
) -> None:
    """Carry a cookie on this response. Secure defaults from the request scheme.

    A script cannot change a response already sent, so this runs during the render that
    produces it. An `http_only` cookie is unreadable and unremovable from the browser.
    """

    request = current_request()
    cookie = Cookie(name, value, path, max_age, secure, samesite, http_only)

    if len(request.response_cookies) >= 32:
        raise CookieError("response cookie limit exceeded")
    request.response_cookies[:] = [
        existing
        for existing in request.response_cookies
        if not (existing.name == cookie.name and existing.path == cookie.path)
    ]
    request.response_cookies.append(cookie)


def delete_cookie(name: str, *, path: str = "/") -> None:
    """Expire a cookie for one path. Removal identity is name plus path."""

    set_cookie(name, "", path=path, max_age=0)


def response_cookie_headers(request: RequestContext) -> tuple[tuple[str, str], ...]:
    """Render the cookies an application set during this render."""

    secure_default = request.scheme == "https"

    return tuple(
        ("set-cookie", cookie.header(secure_default=secure_default))
        for cookie in request.response_cookies
    )
