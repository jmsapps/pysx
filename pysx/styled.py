"""Scoped styles.

CSS text is hashed at import time into a class name and registered once.
Identical CSS collapses to a single rule because the class *is* the digest.
"""

from __future__ import annotations

import hashlib
import textwrap
from dataclasses import dataclass
from string.templatelib import Template
from typing import TYPE_CHECKING, Protocol, cast, overload

from .composition import namespace_for
from .elements import ElementTag
from .reactive import Signal
from .scoped_css import scoped_rules
from .styled_native import NativeFactories
from .styles import style_context, style_owner

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from .render import Fragment

_RULES: dict[str, str] = {}


@dataclass(frozen=True)
class StyledTag:
    tag: str
    css_class: str
    declarations: tuple[str, ...] = ()
    variants: tuple[tuple[str, str, str], ...] = ()

    def __call__(self, *children: object, **attrs: object) -> Fragment:
        from .native_support import element

        variant = attrs.pop("variant", None)
        result = element(self.tag, children, attrs)

        return _decorate(result, self.css_class, self.declarations, self.variants, variant)


@dataclass(frozen=True)
class StyledCallable[**P]:
    base: Callable[P, Template | Fragment]
    css_class: str
    declarations: tuple[str, ...]
    variants: tuple[tuple[str, str, str], ...] = ()

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Fragment:
        from .render import Fragment

        result = self.base(*args, **kwargs)

        if not isinstance(result, (Template, Fragment)):  # pyright: ignore[reportUnnecessaryIsInstance]

            raise TypeError("styled callable bases must return Template or Fragment")
        fragment = result if isinstance(result, Fragment) else Fragment(result)
        fragment = Fragment(
            fragment.template,
            namespace_for(self.base) | dict(fragment.namespace or {}),
            fragment.root_classes,
            fragment.themes,
            fragment.rules,
        )

        return _decorate(fragment, self.css_class, self.declarations, self.variants, None)


class Definition(Protocol):
    @property
    def tag(self) -> str: ...
    @property
    def css_class(self) -> str: ...
    @property
    def declarations(self) -> tuple[str, ...]: ...
    @property
    def variants(self) -> tuple[tuple[str, str, str], ...]: ...


@dataclass(frozen=True)
class VariantClass:
    variants: tuple[tuple[str, str, str], ...]
    source: object

    def __call__(self) -> str:
        source = self.source
        name = cast("Signal[object]", source)() if isinstance(source, Signal) else source

        if name is None:

            return ""

        if not isinstance(name, str):

            raise TypeError("variant requires str, Signal[str] or None")

        for key, cls, _body in self.variants:
            if name == key:

                return cls

        raise ValueError(
            f"unknown variant {name!r}; declared variants: {[v[0] for v in self.variants]}"
        )


def _decorate(
    fragment: Fragment,
    cls: str,
    declarations: tuple[str, ...],
    variants: tuple[tuple[str, str, str], ...],
    variant: object,
) -> Fragment:
    from .render import Fragment

    source = VariantClass(variants, variant)
    source()  # Validate before exposing any markup.
    rules = ((cls, flatten(declarations)), *((name, body) for _key, name, body in variants))

    return Fragment(
        fragment.template,
        fragment.namespace,
        (*fragment.root_classes, cls, source),
        fragment.themes,
        (*fragment.rules, *rules),
    )


@dataclass(frozen=True)
class Extender[T: Definition]:
    base: T

    def __call__(self, css: Template, *, variants: Mapping[str, Template] | None = None) -> T:

        return cast("T", build_styled(cast("StyledTag", self.base), css, variants))


@dataclass(frozen=True)
class CallableExtender[**P]:
    base: Callable[P, Template | Fragment]

    def __call__(
        self, css: Template, *, variants: Mapping[str, Template] | None = None
    ) -> StyledCallable[P]:

        return cast("StyledCallable[P]", build_styled(self.base, css, variants))


class StyledFactory(NativeFactories):
    @overload
    def __call__[T: Definition](self, base: T) -> Extender[T]: ...
    @overload
    def __call__(self, base: ElementTag) -> Extender[StyledTag]: ...
    @overload
    def __call__[**P](self, base: Callable[P, Template | Fragment]) -> CallableExtender[P]: ...

    def __call__(self, base: object) -> object:
        if isinstance(base, ElementTag):
            if base.name == "fragment":

                raise TypeError("styled.fragment has no element to carry a class")

            return Extender(StyledTag(base.name, ""))

        if isinstance(base, StyledTag):

            return Extender(base)

        if callable(base):

            return CallableExtender(cast("Callable[..., Template | Fragment]", base))

        raise TypeError("styled() requires an element, styled object or callable base")


styled = StyledFactory()


def build_styled[**P](
    tag: ElementTag | StyledTag | Callable[P, Template | Fragment],
    css: Template,
    variants: Mapping[str, Template] | None = None,
) -> StyledTag | StyledCallable[P]:
    if not isinstance(tag, (ElementTag, StyledTag)) and not callable(tag):

        raise TypeError("styled() requires an element, styled object or callable base")

    if not isinstance(css, Template):  # pyright: ignore[reportUnnecessaryIsInstance]

        raise TypeError("styled() CSS must be a t-string")

    if css.interpolations:

        raise ValueError("styled() CSS cannot contain interpolations")
    body = textwrap.dedent("".join(css.strings)).strip()

    previous = tag.declarations if isinstance(tag, (StyledTag, StyledCallable)) else ()
    declarations = (*previous, body) if previous or body else ()
    flattened = flatten(declarations)
    cls = "pysx-" + hashlib.sha256(flattened.encode()).hexdigest()[:16]
    scoped_rules(cls, flattened)
    inherited = tag.variants if isinstance(tag, (StyledTag, StyledCallable)) else ()
    variant_bodies = {name: source for name, _cls, source in inherited}

    for name, template in (variants or {}).items():
        if not isinstance(name, str) or not name:  # pyright: ignore[reportUnnecessaryIsInstance]

            raise TypeError("variant names must be nonempty strings")

        if not isinstance(template, Template) or template.interpolations:  # pyright: ignore[reportUnnecessaryIsInstance]

            raise ValueError("variant CSS requires a literal t-string without interpolations")
        source = textwrap.dedent("".join(template.strings)).strip()
        variant_bodies[name] = (
            flatten((variant_bodies[name], source)) if name in variant_bodies else source
        )
    declared = tuple(
        (name, "pysx-" + hashlib.sha256(source.encode()).hexdigest()[:16], source)
        for name, source in variant_bodies.items()
    )

    for _name, variant_cls, source in declared:
        scoped_rules(variant_cls, source)
    registry = style_context.get()

    if registry is None:
        _RULES.setdefault(cls, flattened)

        for _name, variant_cls, source in declared:
            _RULES.setdefault(variant_cls, source)
    else:
        registry.add(style_owner.get(), cls, flattened)

        for _name, variant_cls, source in declared:
            registry.add(style_owner.get(), variant_cls, source)

    if isinstance(tag, (ElementTag, StyledTag)):

        return StyledTag(
            str(tag) if isinstance(tag, ElementTag) else tag.tag, cls, declarations, declared
        )
    base = tag.base if isinstance(tag, StyledCallable) else tag

    return StyledCallable(base, cls, declarations, declared)


_GLOBAL: list[str] = []


def flatten(declarations: tuple[str, ...]) -> str:

    return "\n".join(
        part.strip().rstrip(";") + ";" for part in declarations if part.strip()
    ).rstrip(";")


def global_rules() -> tuple[str, ...]:

    return tuple(_GLOBAL)


def global_style(css: str) -> None:
    """Document-level CSS with ordinary braces, supplied as a plain string."""

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
        out.append(scoped_rules(cls, body))

    return "\n".join(out)


def reset_stylesheet() -> None:
    _RULES.clear()
    _GLOBAL.clear()
