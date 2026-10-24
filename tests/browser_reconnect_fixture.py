"""Real transport recovery, deterministic expiry and tab isolation."""

from __future__ import annotations

import re
from dataclasses import replace
from itertools import count
from typing import TYPE_CHECKING

from pysx import Application, Fragment, HostConfig, TransportLimits, native, signal

if TYPE_CHECKING:
    from pysx.hosting.http import HTTPResult
    from pysx.hosting.request import RequestContext

generations = count(1)


def app() -> Fragment:
    generation = next(generations)
    clicks = signal(0)
    draft = signal("server draft")

    return native.Main(
        native.P(str(generation), id="reconnect-generation"),
        native.P(clicks, id="reconnect-clicks"),
        native.Input(bind_value=draft, id="reconnect-field"),
        native.Button(
            "Increment",
            id="reconnect-increment",
            on_click=lambda _event: clicks.update(lambda value: value + 1),
        ),
    )


host = Application(app)
expired_host = Application(app, config=HostConfig(transport=TransportLimits(resume_ttl=0.05)))


class LegacyApplication(Application):
    async def _http(self, request: RequestContext, *, method: str = "GET") -> HTTPResult:
        response = await super()._http(request, method=method)

        if dict(response.headers).get("content-type", "").startswith("text/html"):
            return replace(
                response,
                body=re.sub(
                    rb' data-pysx-(?:protocol|document|capsule)="[^"]*"',
                    b"",
                    response.body,
                ),
            )

        return response


legacy_expired_host = LegacyApplication(
    app,
    config=HostConfig(
        transport=TransportLimits(resume_ttl=0.05),
    ),
)
