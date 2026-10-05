"""Owned snapshots and typed, writable state projections."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .reactive import Signal, derived

if TYPE_CHECKING:
    from collections.abc import Callable


class Structured[T](Signal[T]):
    """A signal whose callers cannot mutate its owned value in place."""

    def __init__(self, value: T) -> None:
        super().__init__(value)

    def get(self) -> T:
        return super().get()

    __call__ = get

    def set(self, value: T) -> None:
        super().set(value)

    def subscribe(self, fn: Callable[[T], object], *, fire: bool = True) -> Callable[[], None]:
        return super().subscribe(fn, fire=fire)

    def update(self, fn: Callable[[T], T]) -> None:
        self.set(fn(self.get()))


class Projection[T](Signal[T]):
    """A writable lens whose reads track only its selected value."""

    def __init__(self, read: Callable[[], T], write: Callable[[T], None]) -> None:
        super().__init__(cast("T", None))
        self._read = derived(read)
        self._write = write

    def get(self) -> T:
        return self._read()

    __call__ = get

    def set(self, value: T) -> None:
        self._write(value)

    def subscribe(self, fn: Callable[[T], object], *, fire: bool = True) -> Callable[[], None]:
        return self._read.subscribe(fn, fire=fire)

    def update(self, fn: Callable[[T], T]) -> None:
        self.set(fn(self.get()))


def structured[T](value: T) -> Structured[T]:
    return Structured(value)


def project[P, T](
    parent: Signal[P], getter: Callable[[P], T], setter: Callable[[P, T], P]
) -> Projection[T]:
    """The setter receives a private parent snapshot and returns its replacement."""

    # parent() already yields a private snapshot, so the getter reads from and the
    # setter mutates a copy nobody else holds.
    return Projection(
        lambda: getter(parent()),
        lambda value: parent.set(setter(parent(), value)),
    )


def list_index[T](parent: Signal[list[T]], index: int) -> Projection[T]:
    def replace(values: list[T], value: T) -> list[T]:
        values[index] = value

        return values

    return project(parent, lambda values: values[index], replace)


def dict_key[K, T](parent: Signal[dict[K, T]], key: K) -> Projection[T]:
    def replace(values: dict[K, T], value: T) -> dict[K, T]:
        if key not in values:
            raise KeyError(key)

        values[key] = value

        return values

    return project(parent, lambda values: values[key], replace)
