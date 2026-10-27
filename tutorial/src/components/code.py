"""Escaped code spans generated from the editor's Python/pysx grammars."""

import json
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING, cast

from pysx import Fragment, derived, each, pysx

if TYPE_CHECKING:
    from collections.abc import Callable


@cache
def highlighted_sources() -> dict[str, list[list[str]]]:
    return cast(
        "dict[str, list[list[str]]]",
        json.loads(files("src").joinpath("assets/code-tokens.json").read_text()),
    )


def CodeBlock(*, source: str | Callable[[], str], identifier: str | None = None) -> Fragment:
    tokens = derived(
        lambda: tuple(enumerate(highlighted_sources()[source() if callable(source) else source]))
    )

    def token(part: tuple[int, list[str]]) -> Fragment:
        return pysx(t"""
            span(data-syntax={part[1][0]}): {part[1][1]}
        """)

    return pysx(t"""
        pre(id={identifier} class="highlighted-code")
            code: {each(tokens, token, key=lambda part: part[0])}
    """)
