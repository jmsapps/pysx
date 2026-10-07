"""Private typed construction helpers emitted by the template compiler."""

from __future__ import annotations

import linecache
from collections import OrderedDict
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from .composition import Children

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from string.templatelib import Template

    from .reactive import Signal
    from .render import Fragment


@dataclass(frozen=True)
class MissingComponent:
    name: str


def capture[T](name: str, reader: Callable[[], T]) -> T | MissingComponent:
    """Read a lexical binding once; an absent tag fails only when rendered."""

    try:

        return reader()
    except NameError:

        return MissingComponent(name)


def bind(
    fragment: Fragment,
    components: Mapping[str, object],
    validation: object = None,
) -> Fragment:
    """Validation is type-checker input only and is never invoked."""
    del validation

    return replace(
        fragment,
        namespace=MappingProxyType(dict(fragment.namespace or {}) | dict(components)),
        bound=True,
    )


def children() -> Children:

    return Children((), (), MappingProxyType({}))


def component_result(result: Template | Fragment) -> object:

    return result


def variant(value: str | Signal[str] | None) -> object:

    return value


_SOURCES: OrderedDict[str, int] = OrderedDict[str, int]()
_SOURCE_BUDGET = 8 * 1024 * 1024


def register_source(filename: str, source: str) -> None:
    """Bounded standard traceback text for sourceless production artifacts."""
    cost = len(source.encode())

    if cost > _SOURCE_BUDGET:

        return
    _SOURCES.pop(filename, None)

    while _SOURCES and (len(_SOURCES) >= 64 or sum(_SOURCES.values()) + cost > _SOURCE_BUDGET):
        expired, _ = _SOURCES.popitem(last=False)
        linecache.cache.pop(expired, None)
    _SOURCES[filename] = cost
    linecache.cache[filename] = (cost, None, source.splitlines(keepends=True), filename)
