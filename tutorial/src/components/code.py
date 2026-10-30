"""Escaped code spans generated from the editor's Python/pysx grammars."""

from typing import TYPE_CHECKING

from pysx import Fragment, each, pysx

if TYPE_CHECKING:
    from collections.abc import Callable


def CodeBlock(*, source: str | Callable[[], str], identifier: str | None = None) -> Fragment:
    # Source discovery must also work before generated markup exists.
    from .highlighted import highlighted_code

    def display_source(text: str) -> Fragment:
        highlighted = highlighted_code(text)

        if highlighted is not None:
            return highlighted

        return pysx(t"""
            {text}
        """)

    def current_source() -> tuple[str]:
        return (source() if callable(source) else source,)

    return pysx(t"""
        pre(id={identifier} class="highlighted-code")
            code: {each(current_source, display_source, key=lambda _: 0)}
    """)
