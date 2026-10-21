"""Callable local error boundaries with explicit retry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pysx.reactive.reactive import Signal

from .errors import BoundaryState, ErrorInfo

if TYPE_CHECKING:
    from collections.abc import Callable
    from string.templatelib import Template

    from pysx.rendering.render import Fragment


class ErrorBoundary:
    """Render content or a safe fallback; reset recreates only this subtree."""

    def __init__(
        self,
        content: Callable[[], Template | Fragment],
        fallback: Callable[[ErrorInfo, Callable[[], None]], Template | Fragment],
    ) -> None:
        self.content = content
        self.fallback = fallback
        self.state = BoundaryState()
        self.revision = Signal(0)
        self.mounted = False

    @property
    def error(self) -> ErrorInfo | None:
        return self.state.error

    def mount(self) -> None:
        if self.mounted:
            raise RuntimeError("an ErrorBoundary cannot mount in two places at once")

        if not self.state.alive:
            self.state = BoundaryState()
        self.mounted = True

    def unmount(self) -> None:
        self.state.close()
        self.mounted = False

    def reset(self) -> None:
        if self.state.reset(self.state.generation):
            self.revision.set(self.revision() + 1)

    retry = reset

    def __call__(self) -> Template | Fragment:
        raise RuntimeError("ErrorBoundary must be rendered as a component")
