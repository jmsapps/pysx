"""The same prefixed, request-aware application under both host adapters."""

import asyncio

from pysx import (
    Application,
    Fragment,
    HostConfig,
    Link,
    Route,
    Router,
    StaticAsset,
    current_request,
    derived,
    native,
    own_timer,
    resource,
    signal,
)


def app() -> Fragment:
    request = current_request()
    request.document.title = "Shared hosts"
    text = signal("initial")
    ticks = signal(0)

    async def load() -> str:
        await asyncio.sleep(0.1)

        return "loaded"

    value = resource("load", load)
    own_timer("tick", 0.15, lambda: ticks.set(1))
    router = Router(
        [
            Route("/", lambda _: native.P("Home", id="route")),
            Route("/next", lambda _: native.P("Next", id="route")),
        ]
    )

    return native.Main(
        native.H1("Shared hosts"),
        native.P(request.principal or "anonymous", id="principal"),
        native.Input(id="field", bind_value=text),
        native.P(text, id="echo"),
        native.P(ticks, id="ticks"),
        native.P(derived(lambda: value().value or "pending"), id="resource"),
        Link("Next", router=router, href=request.base_path + "/next", id="next"),
        router.view(),
    )


host = Application(
    app,
    config=HostConfig(
        base_path="/demo",
        assets={"/sample.bin": StaticAsset(b"\x00\xffasset", "application/octet-stream", 60)},
        principal=lambda request: request.headers.get("x-user"),
    ),
)
