"""Isolated unified authoring proof using the existing selective graph."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, overload

from pysx.reactive import Signal, derived
from pysx.structured import Structured

if TYPE_CHECKING:
    from collections.abc import Callable


class Unified[T](Structured[T]):
    @overload
    def __getitem__[K, V](self: Unified[dict[K, V]], key: K) -> Unified[V]: ...
    @overload
    def __getitem__[V](self: Unified[list[V]], key: int) -> Unified[V]: ...
    def __getitem__[K, V](self: Unified[dict[K, V]] | Unified[list[V]], key: K | int) -> Unified[V]:
        def read(value: dict[K, V] | list[V]) -> V:
            if isinstance(value, dict):
                return value[cast("K", key)]

            if isinstance(key, int):
                return value[key]

            raise TypeError("index projections require a dictionary key or list integer")

        def write(value: dict[K, V] | list[V], selected: V) -> dict[K, V] | list[V]:
            if isinstance(value, dict):
                values = value
                selected_key = cast("K", key)

                if selected_key not in values:
                    raise KeyError(key)

                values[selected_key] = selected
            elif isinstance(key, int):
                value[key] = selected
            else:
                raise TypeError("index projections require a dictionary key or list integer")

            return value

        parent = cast("Unified[dict[K, V] | list[V]]", self)

        return parent.project(read, write)

    def project[V](self, getter: Callable[[T], V], setter: Callable[[T, V], T]) -> Unified[V]:
        return Lens(lambda: getter(self()), lambda value: self.set(setter(self(), value)))

    @overload
    def __mul__(self: Unified[int], other: int | Signal[int]) -> Signal[int]: ...
    @overload
    def __mul__(self: Unified[float], other: float | Signal[float]) -> Signal[float]: ...
    def __mul__(self, other: object) -> Signal[int] | Signal[float]:
        def compute() -> int | float:
            left = self()
            right = cast("Signal[object]", other)() if isinstance(other, Signal) else other

            if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
                raise TypeError("multiplication requires numeric payloads")

            return left * right

        return cast("Signal[int] | Signal[float]", derived(compute))

    __rmul__ = __mul__


class Lens[T](Unified[T]):
    def __init__(self, read: Callable[[], T], write: Callable[[T], None]) -> None:
        super().__init__(cast("T", None))
        self._read = derived(read)
        self._write = write

    def get(self) -> T:
        from copy import deepcopy

        return deepcopy(self._read())

    __call__ = get

    def set(self, value: T) -> None:
        from copy import deepcopy

        self._write(deepcopy(value))

    def subscribe(self, fn: Callable[[T], object], *, fire: bool = True) -> Callable[[], None]:
        from copy import deepcopy

        return self._read.subscribe(lambda value: fn(deepcopy(value)), fire=fire)


def signal[T](value: T) -> Unified[T]:
    if isinstance(value, Signal):
        raise TypeError("signal payloads cannot be Signals; use a projection or derived()")

    return Unified(value)
