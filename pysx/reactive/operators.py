"""Typed reactive operators and explicit collection helpers."""

from __future__ import annotations

from collections.abc import Container, Sized
from enum import Enum
from typing import TypeGuard, cast, overload

from .reactive import Signal, derived


def read_operand(value: object) -> object:
    return cast("Signal[object]", value).get() if isinstance(value, Signal) else value


def _orderable_number(value: object) -> TypeGuard[int | float]:
    # bool subclasses int; ordering booleans is a mistake, not a comparison.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def order_payload(left: object, right: object, kind: str) -> bool:
    if _orderable_number(left) and _orderable_number(right):
        if kind == "lt":
            return left < right

        if kind == "le":
            return left <= right

        if kind == "gt":
            return left > right

        return left >= right

    if isinstance(left, str) and isinstance(right, str):
        if kind == "lt":
            return left < right

        if kind == "le":
            return left <= right

        if kind == "gt":
            return left > right

        return left >= right

    raise TypeError("Ordering requires numeric pairs or string pairs")


@overload
def eq[T](left: Signal[T], right: T | Signal[T]) -> Signal[bool]: ...
@overload
def eq[T](left: T, right: Signal[T]) -> Signal[bool]: ...
def eq(left: object, right: object) -> Signal[bool]:
    return derived(lambda: read_operand(left) == read_operand(right))


@overload
def ne[T](left: Signal[T], right: T | Signal[T]) -> Signal[bool]: ...
@overload
def ne[T](left: T, right: Signal[T]) -> Signal[bool]: ...
def ne(left: object, right: object) -> Signal[bool]:
    return derived(lambda: read_operand(left) != read_operand(right))


@overload
def lt(
    left: Signal[int] | Signal[float], right: int | float | Signal[int] | Signal[float]
) -> Signal[bool]: ...
@overload
def lt(left: int | float, right: Signal[int] | Signal[float]) -> Signal[bool]: ...
@overload
def lt(left: Signal[str], right: str | Signal[str]) -> Signal[bool]: ...
@overload
def lt(left: str, right: Signal[str]) -> Signal[bool]: ...
def lt(left: object, right: object) -> Signal[bool]:
    return derived(lambda: order_payload(read_operand(left), read_operand(right), "lt"))


@overload
def le(
    left: Signal[int] | Signal[float], right: int | float | Signal[int] | Signal[float]
) -> Signal[bool]: ...
@overload
def le(left: int | float, right: Signal[int] | Signal[float]) -> Signal[bool]: ...
@overload
def le(left: Signal[str], right: str | Signal[str]) -> Signal[bool]: ...
@overload
def le(left: str, right: Signal[str]) -> Signal[bool]: ...
def le(left: object, right: object) -> Signal[bool]:
    return derived(lambda: order_payload(read_operand(left), read_operand(right), "le"))


@overload
def gt(
    left: Signal[int] | Signal[float], right: int | float | Signal[int] | Signal[float]
) -> Signal[bool]: ...
@overload
def gt(left: int | float, right: Signal[int] | Signal[float]) -> Signal[bool]: ...
@overload
def gt(left: Signal[str], right: str | Signal[str]) -> Signal[bool]: ...
@overload
def gt(left: str, right: Signal[str]) -> Signal[bool]: ...
def gt(left: object, right: object) -> Signal[bool]:
    return derived(lambda: order_payload(read_operand(left), read_operand(right), "gt"))


@overload
def ge(
    left: Signal[int] | Signal[float], right: int | float | Signal[int] | Signal[float]
) -> Signal[bool]: ...
@overload
def ge(left: int | float, right: Signal[int] | Signal[float]) -> Signal[bool]: ...
@overload
def ge(left: Signal[str], right: str | Signal[str]) -> Signal[bool]: ...
@overload
def ge(left: str, right: Signal[str]) -> Signal[bool]: ...
def ge(left: object, right: object) -> Signal[bool]:
    return derived(lambda: order_payload(read_operand(left), read_operand(right), "ge"))


def all_of(*values: bool | Signal[bool]) -> Signal[bool]:
    """Derive whether all operands are true, tracking every operand."""

    return derived(lambda: all(_bool_values(values)))


def any_of(*values: bool | Signal[bool]) -> Signal[bool]:
    """Derive whether any operand is true, tracking every operand."""

    return derived(lambda: any(_bool_values(values)))


def not_(value: bool | Signal[bool]) -> Signal[bool]:
    """Derive the negation of a boolean payload."""

    return derived(lambda: not _read_bool(value))


def _bool_values(values: tuple[bool | Signal[bool], ...]) -> tuple[bool, ...]:
    return tuple(_read_bool(value) for value in values)


def _read_bool(value: bool | Signal[bool]) -> bool:
    result = read_operand(value)

    if not isinstance(result, bool):
        raise TypeError("Boolean composition requires bool payloads")

    return result


def concat(left: object, right: object) -> Signal[str]:
    return derived(lambda: str(read_operand(left)) + str(read_operand(right)))


@overload
def length[T: Sized](value: Signal[T]) -> Signal[int]: ...
@overload
def length(value: Sized) -> Signal[int]: ...
def length(value: object) -> Signal[int]:
    def calculate() -> int:
        result = read_operand(value)

        if not isinstance(result, Sized):
            raise TypeError("length requires a sized payload")

        return len(result)

    return derived(calculate)


@overload
def contains[T](container: Signal[list[T]], item: T | Signal[T]) -> Signal[bool]: ...
@overload
def contains[T](container: list[T], item: Signal[T]) -> Signal[bool]: ...
@overload
def contains[T](container: Signal[tuple[T, ...]], item: T | Signal[T]) -> Signal[bool]: ...
@overload
def contains[T](container: tuple[T, ...], item: Signal[T]) -> Signal[bool]: ...
@overload
def contains[T](container: Signal[set[T]], item: T | Signal[T]) -> Signal[bool]: ...
@overload
def contains[T](container: set[T], item: Signal[T]) -> Signal[bool]: ...
@overload
def contains[T](container: Signal[frozenset[T]], item: T | Signal[T]) -> Signal[bool]: ...
@overload
def contains[T](container: frozenset[T], item: Signal[T]) -> Signal[bool]: ...
@overload
def contains(container: Signal[str], item: str | Signal[str]) -> Signal[bool]: ...
@overload
def contains(container: str, item: Signal[str]) -> Signal[bool]: ...
@overload
def contains(container: Signal[range], item: int | Signal[int]) -> Signal[bool]: ...
@overload
def contains(container: range, item: Signal[int]) -> Signal[bool]: ...
def contains(container: object, item: object) -> Signal[bool]:
    def calculate() -> bool:
        result = read_operand(container)

        if not isinstance(result, Container):
            raise TypeError("contains requires a container payload")

        return read_operand(item) in result

    return derived(calculate)


@overload
def inclusive_range(start: int, stop: int) -> range: ...
@overload
def inclusive_range(start: str, stop: str) -> tuple[str, ...]: ...
def inclusive_range(start: object, stop: object) -> range | tuple[str, ...]:
    """Inclusive HSlice endpoints; char slices use single-character strings.

    Python range payloads otherwise remain half-open. Python Enum ordinal slices use
    inclusive_enum_range, with definition order as the explicit ordinal adaptation.
    """

    if isinstance(start, int) and isinstance(stop, int):
        return range(start, stop + 1)

    if isinstance(start, str) and isinstance(stop, str) and len(start) == len(stop) == 1:
        return tuple(chr(value) for value in range(ord(start), ord(stop) + 1))

    raise TypeError("Inclusive range needs integer or single-character endpoints")


def inclusive_enum_range[E: Enum](start: E, stop: E) -> tuple[E, ...]:
    """Python Enum declaration order stands for Nim ordinal ordering."""

    if type(start) is not type(stop):
        raise TypeError("Enum endpoints must share a type")
    values = list(type(start))

    return tuple(values[values.index(start) : values.index(stop) + 1])
