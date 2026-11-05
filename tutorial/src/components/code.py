"""Display authored pysx samples or escaped plain source."""

from typing import TYPE_CHECKING

from pysx import Fragment, each, pysx

from .code_sample import CodeSample

if TYPE_CHECKING:
    from collections.abc import Callable


def CodeBlock(
    *, source: CodeSample | str | Callable[[], CodeSample | str], identifier: str | None = None
) -> Fragment:
    def display_source(sample: CodeSample | str) -> Fragment:
        if isinstance(sample, CodeSample):
            return sample.content()

        return pysx(t"""
            {sample}
        """)

    def current_source() -> tuple[CodeSample | str]:
        return (source() if callable(source) else source,)

    return pysx(t"""
        pre(id={identifier} class="highlighted-code")
            code: {each(current_source, display_source, key=lambda _: 0)}
    """)
