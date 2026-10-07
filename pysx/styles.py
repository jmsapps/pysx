"""Owned CSS rules, reactive custom properties and explicit theme state."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from string.templatelib import Template
from typing import cast

from .reactive import Signal, signal
from .scoped_css import scoped_rules


def css_text(value: object) -> str:
    value = cast("Signal[object]", value)() if isinstance(value, Signal) else value

    if isinstance(value, Template):
        if value.interpolations:

            raise ValueError("CSS interpolations are unsupported; use styleVars or runtime css")
        value = "".join(value.strings)

    if not isinstance(value, str):

        raise TypeError("css requires a string, literal t-string or string signal")
    body = value.strip()

    if "{" in body or "}" in body:

        raise ValueError("css requires flat declarations without braces")

    return body


def css_name(body: str) -> str:

    return "pysx-" + hashlib.sha256(body.encode()).hexdigest()[:16] if body else ""


def css(value: str | Template) -> str:
    """Validate a literal or snapshot flat CSS body; reactive values use the css attribute."""

    if not isinstance(value, (str, Template)):  # pyright: ignore[reportUnnecessaryIsInstance]

        raise TypeError("css() requires literal/snapshot CSS; use a css attribute for signals")

    return css_text(value)


def variable_name(raw: str) -> str:
    name = raw.strip()

    if not name:

        return ""

    if name.startswith("---"):

        raise ValueError("CSS variables cannot have more than two leading hyphens")
    name = "--" + name.lstrip("-")

    if not re.fullmatch(r"--[A-Za-z_][A-Za-z0-9_-]*", name):

        raise ValueError(f"invalid CSS variable name {raw!r}")

    return name


def variable_values(source: object) -> dict[str, str]:
    source = cast("Signal[object]", source)() if isinstance(source, Signal) else source

    if not isinstance(source, Mapping):

        raise TypeError("styleVars requires a mapping")
    result: dict[str, str] = {}

    for raw, value in cast("Mapping[object, object]", source).items():
        if not isinstance(raw, str):

            raise TypeError("CSS variable names must be strings")
        name = variable_name(raw)
        value = cast("Signal[object]", value)() if isinstance(value, Signal) else value

        if not isinstance(value, str):

            raise TypeError("CSS variable values must be strings or string signals")

        if any(char in value for char in ";{}\0"):

            raise ValueError("CSS variable values must contain one property value")

        if name and value:
            result[name] = value

    return result


@dataclass(frozen=True)
class Theme:
    name: str
    variables: tuple[tuple[str, str], ...]


class Themes:
    """Create inside the app function: this state belongs to that render/session."""

    def __init__(self) -> None:
        self.definitions: dict[str, Theme] = {}
        self.names: set[str] = set()
        self.current: Signal[Theme | None] = signal(None)
        self.revision = signal(0)

    def register(self, name: str, variables: Mapping[str, str]) -> Theme:
        name = name.strip()

        if not name:

            raise ValueError("theme names cannot be empty")
        theme = Theme(name, tuple(sorted(variable_values(variables).items())))
        self.definitions[name] = theme
        self.register_vars(variables)

        return theme

    def register_vars(self, names: Mapping[str, object] | tuple[str, ...]) -> None:
        previous = set(self.names)
        self.names.update(name for raw in names if (name := variable_name(raw)))

        if self.names != previous:
            self.revision.set(self.revision() + 1)

    def read(self, name: str) -> Theme:

        return self.definitions[name.strip()]

    def select(self, theme: str | Theme | None) -> None:
        self.current.set(self.read(theme) if isinstance(theme, str) else theme)

    def clear(self) -> None:
        self.select(None)

    def css(self) -> str:
        self.revision()
        current = self.current()
        values = dict(current.variables) if current is not None else {}
        body = "".join(f"{name}:{values.get(name, 'unset')};" for name in sorted(self.names))

        return f":root{{{body}}}" if body else ""


@dataclass
class StyleRegistry:
    rules: dict[str, dict[str, str]] = field(default_factory=dict[str, dict[str, str]])
    globals: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    order: dict[str, None] = field(default_factory=dict[str, None])
    shared: tuple[str, ...] = ()
    theme: str = ""

    def add(self, owner: str, name: str, body: str) -> None:
        if name:
            self.rules.setdefault(owner, {})[name] = body

    def replace(self, owner: str, body: str) -> None:
        self.rules.pop(owner, None)
        self.add(owner, css_name(body), body)

    def apply(self, names: Iterable[str]) -> None:
        for name in names:
            self.order.setdefault(name, None)

    def release(self, prefix: str) -> None:
        for collection in (self.rules, self.globals):
            for owner in tuple(collection):
                if owner.startswith(prefix):
                    del collection[owner]
        live = {name for owned in self.rules.values() for name in owned}
        self.order = {name: None for name in self.order if name in live}

    def snapshot(self) -> str:
        rules: dict[str, str] = {}

        for owned in self.rules.values():
            for name, body in owned.items():
                rules.setdefault(name, body)
        rank = {name: index for index, name in enumerate(self.order)}
        names = sorted(rules, key=lambda name: rank.get(name, len(rank)))
        global_rules = [body for owner in sorted(self.globals) for body in self.globals[owner]]
        scoped = [scoped_rules(name, rules[name]) for name in names]

        return "\n".join((*self.shared, *global_rules, *scoped, self.theme)).strip()

    def close(self) -> None:
        self.rules.clear()
        self.globals.clear()
        self.order.clear()
        self.theme = ""


@dataclass(frozen=True)
class CssClass:
    source: object

    def __call__(self) -> str:

        return css_name(css_text(self.source))


style_context: ContextVar[StyleRegistry | None] = ContextVar("pysx_styles", default=None)
style_owner: ContextVar[str] = ContextVar("pysx_style_owner", default="")
