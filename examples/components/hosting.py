"""Shared host configuration for runnable examples."""

from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING

from pysx import Application, Fragment, HostConfig, StaticAsset
from pysx.hosting.request import request_context

if TYPE_CHECKING:
    from collections.abc import Callable
    from string.templatelib import Template

EXAMPLE_ASSETS: dict[str, StaticAsset] = {
    "/favicon.ico": StaticAsset(
        files("examples.components").joinpath("favicon.png").read_bytes(),
        "image/png",
        cache_seconds=3600,
    )
}


def example_app(view: Callable[[], Template | Fragment] | Application) -> Application:
    if isinstance(view, Application):
        view.assets.update(EXAMPLE_ASSETS)

        return view

    return Application(view, config=HostConfig(assets=EXAMPLE_ASSETS))


def example_title(name: str) -> None:
    context = request_context.get()

    if context is not None:
        context.document.title = f"PYSX - {name}"
