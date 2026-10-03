"""Signals and effects.

Effect bodies must be synchronous. Awaiting inside one, or spawning a task from
it, leaks the current-effect ContextVar into unrelated code and registers
phantom subscriptions.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable

_current: ContextVar["Effect | None"] = ContextVar("pysx_current_effect", default=None)


class Signal:
    __slots__ = ("_value", "_subs", "_derived_from")

    def __init__(self, value: Any) -> None:
        self._value = value
        self._subs: set[Effect] = set()

    def get(self) -> Any:
        eff = _current.get()
        if eff is not None:
            self._subs.add(eff)
            eff._deps.add(self)
        return self._value

    __call__ = get

    def set(self, value: Any) -> None:
        if value == self._value:
            return
        self._value = value
        for eff in list(self._subs):
            eff.run()

    def __repr__(self) -> str:
        return f"Signal({self._value!r})"


class Effect:
    __slots__ = ("_fn", "_deps")

    def __init__(self, fn: Callable[[], None]) -> None:
        self._fn = fn
        self._deps: set[Signal] = set()
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

    def _unsubscribe(self) -> None:
        for sig in self._deps:
            sig._subs.discard(self)
        self._deps.clear()


def signal(value: Any) -> Signal:
    return Signal(value)


def effect(fn: Callable[[], None]) -> Effect:
    return Effect(fn)


def derived(fn: Callable[[], Any]) -> Signal:
    """A signal recomputed from whatever `fn` reads.

    Dependencies are discovered by running fn, so combining several sources
    needs no explicit dependency list.
    """
    out = Signal(None)
    keeper = Effect(lambda: out.set(fn()))
    out._derived_from = keeper  # keep the effect alive for the signal's lifetime
    return out
