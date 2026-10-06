"""Callable namespaces and caller-owned parsed children."""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from inspect import isfunction, ismethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from string.templatelib import Template
    from types import FunctionType

    from .parser import Node
    from .render import Fragment

type Component = Callable[..., Template | Fragment]


def identity_for(fn: Component) -> object:
    target: object = fn

    while isinstance(target, partial):
        target = target.func

    if isfunction(target):
        return target.__code__

    if ismethod(target):
        return target.__func__.__code__

    return type(target)


@dataclass(frozen=True)
class Children:
    """A child block keeps the caller's values and namespace, without a frame."""

    nodes: tuple[Node, ...]
    values: tuple[object, ...]
    namespace: Mapping[str, object]


def namespace_for(fn: Component) -> dict[str, object]:
    """Use defining-module bindings and actual closure cells, never caller frames."""
    target: object = fn

    while isinstance(target, partial):
        target = target.func

    if not isfunction(target):
        target = getattr(target, "__call__", target)  # noqa: B004 - inspect the defining method

    if ismethod(target):
        target = target.__func__
    module = sys.modules.get(getattr(target, "__module__", ""))
    namespace = dict(vars(module)) if module is not None else {}

    if isfunction(target):
        namespace.update(_nonlocals(target))

    return namespace


def _nonlocals(fn: FunctionType) -> dict[str, object]:
    cells = fn.__closure__

    if not cells:
        return {}
    found: dict[str, object] = {}

    for name, cell in zip(fn.__code__.co_freevars, cells, strict=True):
        try:
            found[name] = cell.cell_contents
        except ValueError:
            continue

    return found
