"""Common page layout with optional example-specific declarations."""

from string.templatelib import Template

from pysx import StyledTag, div, styled

from .theme import PAGE_CSS


def page(css: Template | None = None) -> StyledTag:
    """Flatten shared layout and overrides into one predictably ordered rule."""
    if css is not None and css.interpolations:
        raise ValueError("page styles cannot contain interpolations")

    overrides = "" if css is None else "".join(css.strings)

    return styled(div, Template(PAGE_CSS + overrides))


Page = page()
CompactPage = page(t"""width: min(100%, 560px);""")
