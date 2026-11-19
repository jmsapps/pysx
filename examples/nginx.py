"""Reverse-proxy deployment behind nginx, including a mounted application.

One factory builds the host configuration for the parent and the mounted child, so both
inherit the same allowed origins, trusted proxies and cookie policy instead of each
restating them. Defaults are unchanged: nothing here relaxes origin validation, and no
forwarded header is trusted unless the peer is a configured proxy address.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from pysx import Fragment, pysx, signal
from pysx.hosting.hosting import Application, HostConfig
from pysx.hosting.security import SecurityPolicy

from .components import (
    Action,
    Actions,
    Description,
    Eyebrow,
    Metric,
    Page,
    Title,
    Value,
    example_app,
    example_title,
)

if TYPE_CHECKING:
    from pysx.hosting.http import HTTPResult
    from pysx.hosting.request import RequestContext
    from pysx.runtime.connection import Connection

PUBLIC_ORIGIN = os.environ.get("PYSX_ORIGIN", "https://localhost:8443")
TRUSTED_PROXIES = tuple(
    value for value in os.environ.get("PYSX_TRUSTED_PROXIES", "127.0.0.1/32").split(",") if value
)
MOUNT = "/reports"


def deployment_config(**overrides: object) -> HostConfig:
    """The one place proxy trust, origins and cookie policy are decided."""

    # `production=True` additionally requires an authenticated principal and an
    # authorization callback, which belong to an application's own security design
    # rather than to its proxy configuration, so this example stops short of it.
    return HostConfig(
        secure_cookies=True,
        security=SecurityPolicy(origins=(PUBLIC_ORIGIN,), trusted_proxies=TRUSTED_PROXIES),
        **overrides,  # type: ignore[arg-type]
    )


def app() -> Fragment:
    example_title("Behind nginx")
    requests = signal(0)

    def record(_event: object) -> None:
        requests.set(requests() + 1)

    return pysx(t"""
        Page:
            Eyebrow: "Deployment"
            Title: "Served through a reverse proxy"
            Description:
                "This application sits behind nginx, which terminates TLS and forwards
                 the public host and protocol. Cookies are marked secure."
            Metric:
                Value: {requests}
            Actions:
                Action(type="button", onClick={record}): "Count a click"
    """)


def reports() -> Fragment:
    example_title("Reports")

    return pysx(t"""
        Page:
            Eyebrow: "Mounted application"
            Title: "Served under a path prefix"
            Description:
                "A separate application mounted under its own prefix, sharing the parent's
                 origin, proxy trust and cookie policy through one configuration factory."
    """)


class Deployment(Application):
    """Dispatch to the mounted child by prefix, keeping separate application state."""

    def __init__(self) -> None:
        config = deployment_config()
        super().__init__(app, config=config)
        # The child restates nothing: it takes the parent's configuration and changes
        # only what must differ for a separate application under a prefix.
        self.reports = Application(
            reports,
            config=replace(config, base_path=MOUNT, recovery_namespace="pysx-example-reports"),
        )

    async def start(self) -> None:
        await super().start()
        await self.reports.start()

    async def http(self, request: RequestContext, *, method: str = "GET") -> HTTPResult:
        path = urlsplit(request.path).path

        if path == MOUNT or path.startswith(f"{MOUNT}/"):
            return await self.reports.http(request, method=method)

        return await super().http(request, method=method)

    async def websocket(self, connection: Connection) -> None:
        # A session names the page it belongs to in `location`, so the socket is routed
        # the same way its document was; the request path is always /ws.
        location = parse_qs(urlsplit(connection.request.path).query).get("location", [""])[0]
        path = urlsplit(location).path

        if path == MOUNT or path.startswith(f"{MOUNT}/"):
            await self.reports.websocket(connection)

            return
        await super().websocket(connection)

    async def close(self) -> None:
        try:
            await self.reports.close()
        finally:
            await super().close()


example = example_app(Deployment())
