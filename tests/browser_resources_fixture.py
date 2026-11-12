"""Run the resources example with a browser-controlled cancellation gate."""

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import patch

from examples import resources
from pysx import Fragment, Resource, native, resource, signal

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def app() -> Fragment:
    release = asyncio.Event()
    stage = signal("idle")

    def controlled_resource(key: str, loader: Callable[[], Awaitable[str]]) -> Resource[str]:
        requests = 0

        async def load() -> str:
            nonlocal requests
            requests += 1

            if requests != 3:
                return await loader()
            stage.set("held")

            try:
                await release.wait()
            except asyncio.CancelledError:
                # Deliberately finish after cancellation to exercise stale-result rejection.
                stage.set("cancel-observed")
                await release.wait()
            stage.set("finished")

            return "Late cancelled result"

        return resource(key, load)

    with patch.object(resources, "resource", controlled_resource):
        example = replace(resources.app(), namespace=vars(resources))

    return native.Div(
        example,
        native.P(stage, id="load-gate-stage"),
        native.Button("Release load", id="release-load", on_click=lambda _: release.set()),
    )
