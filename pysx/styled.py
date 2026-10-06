"""Scoped styles.

CSS text is hashed at import time into a class name and registered once.
Identical CSS collapses to a single rule because the class *is* the digest.
"""

from __future__ import annotations

import hashlib
import textwrap
from dataclasses import dataclass
from string.templatelib import Template
from typing import TYPE_CHECKING, overload

from .composition import namespace_for
from .elements import ElementTag
from .styles import style_context, style_owner
from .template import dedent_fragments

if TYPE_CHECKING:
    from collections.abc import Callable

    from .render import Fragment

_RULES: dict[str, str] = {}


@dataclass(frozen=True)
class StyledTag:
    tag: str
    css_class: str
    declarations: tuple[str, ...] = ()


@dataclass(frozen=True)
class StyledCallable[**P]:
    base: Callable[P, Template | Fragment]
    css_class: str
    declarations: tuple[str, ...]

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Fragment:
        from .render import Fragment

        result = self.base(*args, **kwargs)

        if not isinstance(result, (Template, Fragment)):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("styled callable bases must return Template or Fragment")
        fragment = result if isinstance(result, Fragment) else Fragment(result)
        registry = style_context.get()

        if registry is not None:
            registry.add(style_owner.get(), self.css_class, flatten(self.declarations))

        return Fragment(
            fragment.template,
            namespace_for(self.base) | dict(fragment.namespace or {}),
            (*fragment.root_classes, self.css_class),
            fragment.themes,
            (*fragment.rules, (self.css_class, flatten(self.declarations))),
        )


@overload
def styled(tag: ElementTag | StyledTag, css: Template) -> StyledTag: ...


@overload
def styled[**P](tag: Callable[P, Template | Fragment], css: Template) -> StyledCallable[P]: ...


def styled[**P](
    tag: ElementTag | StyledTag | Callable[P, Template | Fragment], css: Template
) -> StyledTag | StyledCallable[P]:
    if not isinstance(tag, (ElementTag, StyledTag)) and not callable(tag):
        raise TypeError("styled() requires an element, styled object or callable base")

    if not isinstance(css, Template):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError("styled() CSS must be a t-string")

    if css.interpolations:
        raise ValueError("styled() CSS cannot contain interpolations")
    body = "\n".join(dedent_fragments(css.strings)).strip()

    if "{" in body or "}" in body:
        raise ValueError("styled() CSS requires flat declarations without braces")
    previous = tag.declarations if isinstance(tag, (StyledTag, StyledCallable)) else ()
    declarations = (*previous, body)
    flattened = flatten(declarations)
    cls = "pysx-" + hashlib.sha256(flattened.encode()).hexdigest()[:16]
    registry = style_context.get()

    if registry is None:
        _RULES.setdefault(cls, flattened)
    else:
        registry.add(style_owner.get(), cls, flattened)

    if isinstance(tag, (ElementTag, StyledTag)):
        return StyledTag(str(tag) if isinstance(tag, ElementTag) else tag.tag, cls, declarations)
    base = tag.base if isinstance(tag, StyledCallable) else tag

    return StyledCallable(base, cls, declarations)


_GLOBAL: list[str] = []


def flatten(declarations: tuple[str, ...]) -> str:
    return ";\n".join(part.strip().rstrip(";") for part in declarations)


def global_rules() -> tuple[str, ...]:
    return tuple(_GLOBAL)


def global_style(css: str) -> None:
    """Unscoped CSS, for rules a per-class hash cannot express (:root, body,
    and modifier selectors like `.base.is-active`).

    Takes a plain string, not a t-string: CSS block braces would otherwise have
    to be doubled, since `{` opens an interpolation. `styled()` avoids the
    problem by accepting flat property lists with no braces at all.
    """

    if not isinstance(css, str):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError("global_style() takes a plain string, not a t-string")
    body = textwrap.dedent(css).strip()
    registry = style_context.get()

    if registry is None:
        if body not in _GLOBAL:
            _GLOBAL.append(body)
    else:
        owned = registry.globals.setdefault(style_owner.get(), [])

        if body not in owned:
            owned.append(body)


def stylesheet() -> str:
    registry = style_context.get()

    if registry is not None:
        return registry.snapshot()
    out = list(_GLOBAL)

    for cls, body in sorted(_RULES.items()):
        rules = "\n".join("  " + ln.strip() for ln in body.splitlines() if ln.strip())
        out.append(f".{cls} {{\n{rules}\n}}")

    return "\n".join(out)


def reset_stylesheet() -> None:
    _RULES.clear()
    _GLOBAL.clear()
