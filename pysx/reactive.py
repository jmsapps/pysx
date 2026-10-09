"""Synchronous, identity-tracked signals and settled computations."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from threading import get_ident
from typing import TYPE_CHECKING, Never, Protocol, cast, overload

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable

_current: ContextVar[Effect | None] = ContextVar("pysx_current_effect", default=None)


class Dependency(Protocol):
    def unsubscribe(self, effect: Effect) -> None: ...


class Readable[T](Protocol):
    def __call__(self) -> T: ...


class _Scheduler:
    def __init__(self) -> None:
        self.computed: dict[int, Effect] = {}
        self.observers: dict[int, Effect] = {}
        self.running = False
        self.depth = 0
        self.owner: tuple[int, int] | None = None

    def check_owner(self) -> None:
        if self.owner is not None and self.owner != _execution_owner():
            raise RuntimeError("batch cannot cross a thread or async task boundary")

    def enqueue(self, observer: Effect) -> None:
        queue = self.computed if observer.computed is not None else self.observers
        queue[id(observer)] = observer

    def dequeue(self, observer: Effect) -> None:
        self.computed.pop(id(observer), None)
        self.observers.pop(id(observer), None)

    def flush(self) -> None:
        if self.running or self.depth:
            return

        self.running = True
        errors: list[Exception] = []
        evaluations: dict[int, int] = {}

        try:
            while self.computed or self.observers:
                queue = self.computed or self.observers
                key = next(iter(queue))
                observer = queue.pop(key)
                # Per node, not per flush: a wide fan-out is not feedback.
                evaluations[key] = evaluations.get(key, 0) + 1

                if evaluations[key] > 1000:
                    self.computed.clear()
                    self.observers.clear()
                    errors.append(RuntimeError("reactive feedback exceeded 1000 evaluations"))

                    break

                try:
                    observer.run()
                except Exception as exc:
                    errors.append(exc)
        except BaseException:
            self.computed.clear()
            self.observers.clear()

            raise
        finally:
            self.running = False

        if errors:
            raise ExceptionGroup("reactive transaction failed", errors)


_scheduler = _Scheduler()


def _execution_owner() -> tuple[int, int]:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None

    return get_ident(), id(task)


@contextmanager
def untracked() -> Generator[None]:
    """Read signals without subscribing whatever computation is currently running."""
    token = _current.set(None)

    try:
        yield
    finally:
        _current.reset(token)


@contextmanager
def batch() -> Generator[None]:
    """Coalesce a synchronous write turn; committed writes are never rolled back."""
    _scheduler.check_owner()
    previous_owner = _scheduler.owner
    _scheduler.owner = _execution_owner()
    _scheduler.depth += 1
    body_error: BaseException | None = None

    try:
        yield
    except BaseException as exc:
        body_error = exc

        raise
    finally:
        _scheduler.depth -= 1

        # Effect evaluation also holds depth; ownership follows batch lifetime.
        _scheduler.owner = previous_owner

        try:
            _scheduler.flush()
        except BaseException as exc:
            if body_error is not None:
                raise BaseExceptionGroup(
                    "batch body and observers failed", [body_error, exc]
                ) from None

            raise


def _reject_nested_signals(value: object, seen: set[int] | None = None) -> None:
    if isinstance(value, Signal):
        raise TypeError("signal payloads cannot be Signals; use a projection or derived()")

    seen = set() if seen is None else seen
    marker = id(value)

    if marker in seen:
        return

    if isinstance(value, dict):
        seen.add(marker)

        for key, item in cast("dict[object, object]", value).items():
            _reject_nested_signals(key, seen)
            _reject_nested_signals(item, seen)
    elif isinstance(value, (list, tuple, set, frozenset)):
        seen.add(marker)

        for item in cast("Iterable[object]", value):
            _reject_nested_signals(item, seen)


class Signal[T]:
    def __init__(self, value: T, *, equal: Callable[[T, T], bool] | None = None) -> None:
        _reject_nested_signals(value)

        self._value = deepcopy(value)
        # Equality is answered against the value as published, not against the owned
        # copy: a copy is never identity-equal, so an `is`-based comparator could
        # never suppress. Callers must replace values rather than mutate in place.
        self._published: T = value
        self.observers: dict[int, Effect] = {}
        self.computation: Effect | None = None
        self._equal: Callable[[T, T], bool] = equal or (lambda left, right: left == right)
        self.dirty = False
        self.initialized = True
        self._reader: Effect | None = None

    def get(self) -> T:
        _scheduler.check_owner()
        eff = _current.get()
        keeper = self.computation

        if keeper is not None and keeper.running:
            raise RuntimeError("cyclic reactive computation")

        if eff is not None:
            self.observers[id(eff)] = eff
            eff.track(self)

        if keeper is not None and (self.dirty or not self.observers):
            if not self.observers:
                self.dirty = True

            reader, self._reader = self._reader, eff

            try:
                keeper.run()
            finally:
                self._reader = reader

        return deepcopy(self._value)

    __call__ = get

    def publish(self, value: T) -> None:
        _reject_nested_signals(value)

        if self.initialized and self._equal(value, self._published):
            return

        self.initialized = True
        self._published = value
        self._value = deepcopy(value)

        for eff in tuple(self.observers.values()):
            # The reader this evaluation is serving returns the fresh value from
            # get(); every other observer must still be invalidated, even mid-run.
            if eff is not self._reader:
                eff.invalidate()

    def set(self, value: T) -> None:
        _scheduler.check_owner()

        if self.computation is not None:
            raise TypeError("derived signals are read-only")

        self.publish(value)
        _scheduler.flush()

    def __repr__(self) -> str:
        return f"Signal({self._value!r})"

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
        from .operators import order_payload, read_operand

        return derived(lambda: order_payload(self.get(), read_operand(other), "lt"))

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
        from .operators import order_payload, read_operand

        return derived(lambda: order_payload(self.get(), read_operand(other), "le"))

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
        from .operators import order_payload, read_operand

        return derived(lambda: order_payload(self.get(), read_operand(other), "gt"))

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
        from .operators import order_payload, read_operand

        return derived(lambda: order_payload(self.get(), read_operand(other), "ge"))

    @overload
    def __getitem__[K, V](self: Signal[dict[K, V]], key: K) -> Signal[V]: ...
    @overload
    def __getitem__[V](self: Signal[list[V]], key: int) -> Signal[V]: ...
    def __getitem__[K, V](self: Signal[dict[K, V]] | Signal[list[V]], key: K | int) -> Signal[V]:
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

        parent = cast("Signal[dict[K, V] | list[V]]", self)

        return parent.project(read, write)

    def project[V](self, getter: Callable[[T], V], setter: Callable[[T, V], T]) -> Signal[V]:
        from .structured import project

        return project(self, getter, setter)

    @overload
    def __mul__(self: Signal[int], other: int | Signal[int]) -> Signal[int]: ...
    @overload
    def __mul__(self: Signal[float], other: float | Signal[float]) -> Signal[float]: ...
    def __mul__(self, other: object) -> Signal[int] | Signal[float]:
        def compute() -> int | float:
            left = self()
            right = cast("Signal[object]", other)() if isinstance(other, Signal) else other

            if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
                raise TypeError("multiplication requires numeric payloads")

            return left * right

        return cast("Signal[int] | Signal[float]", derived(compute))

    __rmul__ = __mul__

    def update(self, fn: Callable[[T], T]) -> None:
        self.set(fn(self.get()))

    def __contains__(self, item: Never) -> bool:
        raise TypeError("Use contains(signal, item); read signal() for a snapshot")

    def unsubscribe(self, effect: Effect) -> None:
        self.observers.pop(id(effect), None)

        if not self.observers and self.computation is not None:
            _scheduler.dequeue(self.computation)
            self.computation.detach()
            self.dirty = True

    def subscribe(self, fn: Callable[[T], object], *, fire: bool = True) -> Callable[[], None]:
        first = True
        previous = cast("T", None)

        def notify() -> None:
            nonlocal first, previous
            value = self.get()
            token = _current.set(None)

            try:
                if (first and fire) or (not first and not self._equal(value, previous)):
                    fn(deepcopy(value))
            finally:
                previous = value
                first = False
                _current.reset(token)

        return Effect(notify).dispose


class Effect:
    def __init__(self, fn: Callable[[], object], *, computed: Signal[object] | None = None) -> None:
        self._fn = fn
        self._deps: dict[int, Dependency] = {}
        self.computed = computed
        self._disposed = False
        self.running = False
        self._cleanup: Callable[[], object] | None = None

        if computed is None:
            try:
                self.run()
            except BaseException:
                self.dispose()

                raise

    def invalidate(self) -> None:
        if self._disposed:
            return

        if self.computed is not None:
            self.mark_dirty()

        _scheduler.enqueue(self)

    def mark_dirty(self) -> None:
        if self.computed is None or self.computed.dirty:
            return

        self.computed.dirty = True

        for observer in tuple(self.computed.observers.values()):
            observer.mark_dirty()

    def run(self) -> None:
        if self._disposed:
            return

        if self.running:
            raise RuntimeError("reentrant reactive evaluation")

        if self.computed is not None and not self.computed.dirty:
            return

        self.running = True
        _scheduler.depth += 1
        previous = self._deps
        self._deps = {}
        token = _current.set(self)

        try:
            cleanup, self._cleanup = self._cleanup, None

            if cleanup is not None:
                cleanup_token = _current.set(None)

                try:
                    cleanup()
                finally:
                    _current.reset(cleanup_token)

            result = self._fn()

            if callable(result):
                self._cleanup = cast("Callable[[], object]", result)

            if self.computed is not None:
                self.computed.dirty = False
        except BaseException:
            self._deps.update(previous)

            raise
        finally:
            _current.reset(token)
            _scheduler.depth -= 1
            self.running = False

            for key, dependency in previous.items():
                if key not in self._deps:
                    dependency.unsubscribe(self)

            if self.computed is not None and not self.computed.observers:
                self.detach()
                self.computed.dirty = True

            # Outermost evaluation: drain whatever this body invalidated. A run
            # started by the drain loop leaves it to that loop.

            if not _scheduler.running and not _scheduler.depth:
                _scheduler.flush()

    def dispose(self) -> None:
        self._disposed = True
        _scheduler.dequeue(self)
        self.detach()
        cleanup, self._cleanup = self._cleanup, None

        if cleanup is not None:
            cleanup()

    def track(self, dependency: Dependency) -> None:
        self._deps[id(dependency)] = dependency

    def detach(self) -> None:
        previous, self._deps = self._deps, {}

        for dependency in previous.values():
            dependency.unsubscribe(self)


def signal[T](value: T, *, equal: Callable[[T, T], bool] | None = None) -> Signal[T]:
    return Signal(value, equal=equal)


def effect(fn: Callable[[], object]) -> Effect:
    return Effect(fn)


def derived[T](fn: Callable[[], T], *, equal: Callable[[T, T], bool] | None = None) -> Signal[T]:
    """Lazily compute a value from every signal read by the callable."""
    out = Signal(cast("T", None), equal=equal)
    out.dirty = True
    out.initialized = False
    out.computation = Effect(lambda: out.publish(fn()), computed=cast("Signal[object]", out))

    return out
