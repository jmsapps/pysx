"""Signals and effects.

Effect bodies must be synchronous. Awaiting inside one, or spawning a task from
it, leaks the current-effect ContextVar into unrelated code and registers
phantom subscriptions.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable

_current: ContextVar[Effect | None] = ContextVar("pysx_current_effect", default=None)


class Dependency(Protocol):
    def unsubscribe(self, effect: Effect) -> None: ...


class Readable[T](Protocol):
    def __call__(self) -> T: ...


class Signal[T]:
    __slots__ = ("_derived_from", "_subs", "_value")

    def __init__(self, value: T) -> None:
        self._value = value
        self._subs: set[Effect] = set()
        self._derived_from: Effect | None = None

    def get(self) -> T:
        eff = _current.get()
        if eff is not None:
            self._subs.add(eff)
            eff.track(self)
        return self._value

    __call__ = get

    def set(self, value: T) -> None:
        if value == self._value:
            return
        self._value = value
        for eff in list(self._subs):
            eff.run()

    def __repr__(self) -> str:
        return f"Signal({self._value!r})"

    def unsubscribe(self, effect: Effect) -> None:
        self._subs.discard(effect)

    def retain(self, effect: Effect) -> None:
        self._derived_from = effect


class Effect:
    __slots__ = ("_deps", "_fn")

    def __init__(self, fn: Callable[[], object]) -> None:
        self._fn = fn
        self._deps: set[Dependency] = set()
        self.run()

    def run(self) -> None:
        # Dependencies are rebuilt every run so a branch that stops reading a
        # signal also stops being woken by it.
        self._unsubscribe()
        token = _current.set(self)
        try:
            self._fn()
        finally:
            _current.reset(token)

    def dispose(self) -> None:
        self._unsubscribe()

    def track(self, dependency: Dependency) -> None:
        self._deps.add(dependency)

    def _unsubscribe(self) -> None:
        for sig in self._deps:
            sig.unsubscribe(self)
        self._deps.clear()


def signal[T](value: T) -> Signal[T]:
    return Signal(value)


def effect(fn: Callable[[], object]) -> Effect:
    return Effect(fn)


def derived[T](fn: Callable[[], T]) -> Signal[T]:
    """A signal recomputed from whatever `fn` reads.

    Dependencies are discovered by running fn, so combining several sources
    needs no explicit dependency list.
    """
    # The synchronous first effect run replaces this inaccessible seed before return.
    out = Signal(cast("T", None))
    keeper = Effect(lambda: out.set(fn()))
    out.retain(keeper)
    return out
