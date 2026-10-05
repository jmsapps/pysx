"""Scoped styles.

CSS text is hashed at import time into a class name and registered once.
Identical CSS collapses to a single rule because the class *is* the digest.
"""

from __future__ import annotations

import hashlib
import textwrap
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .template import dedent_fragments

if TYPE_CHECKING:
    from string.templatelib import Template

    from .elements import ElementTag

_RULES: dict[str, str] = {}


@dataclass(frozen=True)
class StyledTag:
    tag: str
    css_class: str


def styled(tag: str | ElementTag, css: Template) -> StyledTag:
    if css.interpolations:
        raise ValueError("styled() CSS cannot contain interpolations")
    body = "\n".join(dedent_fragments(css.strings)).strip()
    cls = "pysx-" + hashlib.sha256(body.encode()).hexdigest()[:6]
    _RULES[cls] = body

    return StyledTag(tag=str(tag), css_class=cls)


_GLOBAL: list[str] = []


def global_style(css: str) -> None:
    """Unscoped CSS, for rules a per-class hash cannot express (:root, body,
    and modifier selectors like `.base.is-active`).

    Takes a plain string, not a t-string: CSS block braces would otherwise have
    to be doubled, since `{` opens an interpolation. `styled()` avoids the
    problem by accepting flat property lists with no braces at all.
    """

    if not isinstance(css, str):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError("global_style() takes a plain string, not a t-string")
    _GLOBAL.append(textwrap.dedent(css).strip())


def stylesheet() -> str:
    out = list(_GLOBAL)

    for cls, body in sorted(_RULES.items()):
        rules = "\n".join("  " + ln.strip() for ln in body.splitlines() if ln.strip())
        out.append(f".{cls} {{\n{rules}\n}}")

    return "\n".join(out)


def reset_stylesheet() -> None:
    _RULES.clear()
    _GLOBAL.clear()
