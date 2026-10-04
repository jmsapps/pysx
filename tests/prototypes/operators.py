"""Isolated operator feasibility proof; not a public pysx implementation.

Reactive equality uses eq/ne. Dunder equality is identity and returns bool.
Primitive bool/len/in protocols never claim to produce a reactive value.
"""

from __future__ import annotations

from collections.abc import Container, Sized
from contextvars import ContextVar
from enum import Enum
from typing import TYPE_CHECKING, Protocol, cast, overload

if TYPE_CHECKING:
    from collections.abc import Callable


class Dependency(Protocol):
    def attach(self, observer: Observer) -> None: ...
    def detach(self, observer: Observer) -> None: ...


_current: ContextVar[Observer | None] = ContextVar("operator_proof_observer", default=None)


class Signal[T]:
    def __init__(self, value: T) -> None:
        if isinstance(value, Signal):
            raise TypeError("nested Signals are not payloads")
        self.value = value
        self.observers: dict[int, Observer] = {}
        self.keeper: Observer | None = None

    def get(self) -> T:
        observer = _current.get()
        if observer is not None:
            observer.dependencies[id(self)] = self
            self.attach(observer)
        return self.value

    __call__ = get

    def set(self, value: T) -> None:
        if isinstance(value, Signal):
            raise TypeError("nested Signals are not payloads")
        if value == self.value:
            return
        self.value = value
        for observer in tuple(self.observers.values()):
            observer.run()

    def attach(self, observer: Observer) -> None:
        self.observers[id(observer)] = observer

    def detach(self, observer: Observer) -> None:
        self.observers.pop(id(observer), None)

    def dispose(self) -> None:
        if self.keeper is not None:
            self.keeper.dispose()

    def __eq__(self, other: object) -> bool:
        return self is other

    def __bool__(self) -> bool:
        raise TypeError("Use all_of/any_of/not_ and eq/ne; read get() for a snapshot")

    @overload
    def __lt__(
        self: Signal[int], other: int | float | Signal[int] | Signal[float]
    ) -> Signal[bool]: ...
    @overload
    def __lt__(
        self: Signal[float], other: int | float | Signal[int] | Signal[float]
    ) -> Signal[bool]: ...
    @overload
    def __lt__(self: Signal[str], other: str | Signal[str]) -> Signal[bool]: ...
    def __lt__(self, other: object) -> Signal[bool]:
        return derived(lambda: _order(self.get(), _read(other), "lt"))

    @overload
    def __le__(
        self: Signal[int], other: int | float | Signal[int] | Signal[float]
    ) -> Signal[bool]: ...
    @overload
    def __le__(
        self: Signal[float], other: int | float | Signal[int] | Signal[float]
    ) -> Signal[bool]: ...
    @overload
    def __le__(self: Signal[str], other: str | Signal[str]) -> Signal[bool]: ...
    def __le__(self, other: object) -> Signal[bool]:
        return derived(lambda: _order(self.get(), _read(other), "le"))

    @overload
    def __gt__(
        self: Signal[int], other: int | float | Signal[int] | Signal[float]
    ) -> Signal[bool]: ...
    @overload
    def __gt__(
        self: Signal[float], other: int | float | Signal[int] | Signal[float]
    ) -> Signal[bool]: ...
    @overload
    def __gt__(self: Signal[str], other: str | Signal[str]) -> Signal[bool]: ...
    def __gt__(self, other: object) -> Signal[bool]:
        return derived(lambda: _order(self.get(), _read(other), "gt"))

    @overload
    def __ge__(
        self: Signal[int], other: int | float | Signal[int] | Signal[float]
    ) -> Signal[bool]: ...
    @overload
    def __ge__(
        self: Signal[float], other: int | float | Signal[int] | Signal[float]
    ) -> Signal[bool]: ...
    @overload
    def __ge__(self: Signal[str], other: str | Signal[str]) -> Signal[bool]: ...
    def __ge__(self, other: object) -> Signal[bool]:
        return derived(lambda: _order(self.get(), _read(other), "ge"))

    def __and__(self: Signal[bool], other: bool | Signal[bool]) -> Signal[bool]:
        return all_of(self, other)

    __rand__ = __and__

    def __or__(self: Signal[bool], other: bool | Signal[bool]) -> Signal[bool]:
        return any_of(self, other)

    __ror__ = __or__

    def __invert__(self: Signal[bool]) -> Signal[bool]:
        return not_(self)


class Observer:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.dependencies: dict[int, Dependency] = {}
        self.run()

    def dispose(self) -> None:
        for dependency in self.dependencies.values():
            dependency.detach(self)
        self.dependencies.clear()

    def run(self) -> None:
        self.dispose()
        token = _current.set(self)
        try:
            self.callback()
        finally:
            _current.reset(token)


def derived[T](callback: Callable[[], T]) -> Signal[T]:
    # The provisional value is inaccessible until the synchronous observer initializes it.
    result = Signal(cast("T", None))
    result.keeper = Observer(lambda: result.set(callback()))
    return result


def _read(value: object) -> object:
    return cast("Signal[object]", value).get() if isinstance(value, Signal) else value


def _order(left: object, right: object, kind: str) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
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
    return derived(lambda: _read(left) == _read(right))


@overload
def ne[T](left: Signal[T], right: T | Signal[T]) -> Signal[bool]: ...
@overload
def ne[T](left: T, right: Signal[T]) -> Signal[bool]: ...
def ne(left: object, right: object) -> Signal[bool]:
    return derived(lambda: _read(left) != _read(right))


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
    return derived(lambda: _order(_read(left), _read(right), "lt"))


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
    return derived(lambda: _order(_read(left), _read(right), "le"))


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
    return derived(lambda: _order(_read(left), _read(right), "gt"))


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
    return derived(lambda: _order(_read(left), _read(right), "ge"))


def all_of(*values: bool | Signal[bool]) -> Signal[bool]:
    # Read all operands before combining; a false left operand cannot hide dependencies.
    return derived(lambda: all(_bool_values(values)))


def any_of(*values: bool | Signal[bool]) -> Signal[bool]:
    return derived(lambda: any(_bool_values(values)))


def not_(value: bool | Signal[bool]) -> Signal[bool]:
    return derived(lambda: not _read_bool(value))


def _bool_values(values: tuple[bool | Signal[bool], ...]) -> tuple[bool, ...]:
    return tuple(_read_bool(value) for value in values)


def _read_bool(value: bool | Signal[bool]) -> bool:
    result = _read(value)
    if not isinstance(result, bool):
        raise TypeError("Boolean composition requires bool payloads")
    return result


def concat(left: object, right: object) -> Signal[str]:
    return derived(lambda: str(_read(left)) + str(_read(right)))


@overload
def length[T: Sized](value: Signal[T]) -> Signal[int]: ...
@overload
def length(value: Sized) -> Signal[int]: ...
def length(value: object) -> Signal[int]:
    def calculate() -> int:
        result = _read(value)
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
        result = _read(container)
        if not isinstance(result, Container):
            raise TypeError("contains requires a container payload")
        return _read(item) in result

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
