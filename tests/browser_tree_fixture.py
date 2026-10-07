"""Recursive composition verification uses the shared public example controls."""

from typing import TYPE_CHECKING

from pysx.loader import install_loader

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysx import Fragment


def _load_app() -> Callable[[], Fragment]:
    install_loader(packages=("examples",))
    from examples.components.composition import tree_controls

    return tree_controls


app = _load_app()


__all__ = ["app"]
