"""Common page layout with optional example-specific declarations."""

from __future__ import annotations

from string.templatelib import Template
from typing import TYPE_CHECKING

from pysx import styled

if TYPE_CHECKING:
    from pysx.styled_native import StyledDiv

from .theme import PAGE_CSS


def page(css: Template | None = None) -> StyledDiv:
    """Flatten shared layout and overrides into one predictably ordered rule."""

    if css is not None and css.interpolations:

        raise ValueError("page styles cannot contain interpolations")

    overrides = "" if css is None else "".join(css.strings)

    return styled.div(Template(PAGE_CSS + overrides))


Page = page()
CompactPage = page(t"""width: min(100%, 560px);""")
