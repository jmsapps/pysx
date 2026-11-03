"""Code samples authored as pysx; their visible text is also their runnable source."""

from dataclasses import dataclass
from functools import cached_property
from html.parser import HTMLParser
from typing import TYPE_CHECKING

from pysx import render

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysx import Fragment


class _CodeText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


@dataclass(frozen=True)
class CodeSample:
    content: Callable[[], Fragment]

    @cached_property
    def text(self) -> str:
        rendered = render(self.content)

        try:
            parser = _CodeText()
            parser.feed(rendered.body)

            return "".join(parser.parts)
        finally:
            rendered.dispose()
