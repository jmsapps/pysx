"""Explicit typed lexical bindings and bounded developer-authored builders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

MAX_ROWS = 10000


@dataclass(frozen=True, eq=False)
class Binding[T]:
    name: str


@dataclass(frozen=True)
class Environment:
    values: tuple[tuple[Binding[object], object], ...] = ()
    parent: Environment | None = None

    def get[T](self, binding: Binding[T]) -> T:
        for declaration, value in self.values:
            if declaration is binding:

                return cast("T", value)

        if self.parent is not None:

            return self.parent.get(binding)

        raise KeyError(f"unbound lexical binding {binding.name!r}")

    def child(
        self, bindings: tuple[Binding[object], ...], values: tuple[object, ...]
    ) -> Environment:
        if len(bindings) != len(values):

            raise ValueError("destructured binding arity mismatch")

        return Environment(tuple(zip(bindings, values, strict=True)), self)


@dataclass(frozen=True)
class Deferred[T]:
    evaluate: Callable[[Environment], T]

    def map[R](self, transform: Callable[[T], R]) -> Deferred[R]:

        return Deferred(lambda environment: transform(self.evaluate(environment)))


def defer[A, T](binding: Binding[A], expression: Callable[[A], T]) -> Deferred[T]:

    return Deferred(lambda environment: expression(environment.get(binding)))


def defer2[A, B, T](
    first: Binding[A], second: Binding[B], expression: Callable[[A, B], T]
) -> Deferred[T]:

    return Deferred(lambda environment: expression(environment.get(first), environment.get(second)))


def resolve(value: object, environment: Environment) -> object:
    for _ in range(16):
        if isinstance(value, Binding):
            value = environment.get(cast("Binding[object]", value))
        elif isinstance(value, Deferred):
            value = cast("Deferred[object]", value).evaluate(environment)
        else:

            return value

    raise ValueError("cyclic or excessively nested deferred binding")


def bounded_while[T](
    condition: Callable[[], bool], builder: Callable[[], T], *, limit: int = MAX_ROWS
) -> tuple[T, ...]:
    """Run an ordinary bounded loop once; returned entries are snapshots."""

    if not 0 <= limit <= MAX_ROWS:

        raise ValueError("while limit must be between 0 and 10000")
    results: list[T] = []

    while len(results) < limit:
        if not condition():

            return tuple(results)
        results.append(builder())

    if condition():

        raise ValueError(f"ordinary while loop exceeded limit {limit}")

    return tuple(results)
