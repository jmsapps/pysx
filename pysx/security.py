"""Explicit application authorization and network trust boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Callable

    from .request import RequestContext


class AccessDenied(PermissionError):  # noqa: N818 - public authorization outcome
    """A safe denial without application credentials or callback details."""


def origin(value: str) -> str:
    parsed = urlsplit(value)

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in value)
        or "\\" in value
        or "," in value
    ):
        raise ValueError("invalid origin")
    port = parsed.port
    host = parsed.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    suffix = (
        f":{port}" if port is not None and port != (443 if parsed.scheme == "https" else 80) else ""
    )

    return f"{parsed.scheme}://{host}{suffix}"


@dataclass(frozen=True)
class SecurityPolicy:
    origins: tuple[str, ...] = ()
    trusted_proxies: tuple[str, ...] = ()
    require_principal: bool = False
    authorize: Callable[[RequestContext, str, str], bool] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "origins", tuple(origin(value) for value in self.origins))

        for network in self.trusted_proxies:
            ip_network(network)

    def trusts(self, peer: str | None) -> bool:
        return peer is not None and any(
            ip_address(peer) in ip_network(network) for network in self.trusted_proxies
        )

    def check_network(self, request: RequestContext, *, websocket: bool) -> None:
        try:
            host = request.headers.get("host", "")
            expected = origin(f"{request.scheme}://{host}") if host else None
            supplied = request.headers.get("origin")

            if self.origins:
                if expected not in self.origins:
                    raise AccessDenied
            elif host:
                hostname = urlsplit(expected or "").hostname

                if hostname not in ("localhost", "127.0.0.1", "::1"):
                    raise AccessDenied

            if (
                not self.origins
                and request.peer is not None
                and not ip_address(request.peer).is_loopback
            ):
                raise AccessDenied

            if supplied is not None:
                actual = origin(supplied)

                if actual != expected and actual not in self.origins:
                    raise AccessDenied
            elif websocket and self.origins:
                raise AccessDenied

            if self.require_principal and not request.principal:
                raise AccessDenied
        except ValueError, AccessDenied:
            raise AccessDenied("access denied") from None

    def check(self, request: RequestContext, action: str, target: str = "") -> None:
        try:
            if self.require_principal and not request.principal:
                raise AccessDenied

            if self.authorize is not None and self.authorize(request, action, target) is not True:
                raise AccessDenied
        except AccessDenied:
            raise AccessDenied("access denied") from None
        except Exception as error:
            from .errors import ErrorInfo, report_error

            report_error(ErrorInfo(None, "handler"), error)

            raise AccessDenied("access denied") from None


def require_access(action: str, target: str = "") -> None:
    """Authorize an application operation using the current session's identity."""
    from .request import current_request

    request = current_request()

    if request.access is not None:
        request.access(action, target)
