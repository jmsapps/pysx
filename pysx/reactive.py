"""Synchronous, identity-tracked signals and settled computations."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from threading import get_ident
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

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
def batch() -> Generator[None]:
    """Coalesce a synchronous write turn; committed writes are never rolled back."""
    _scheduler.check_owner()
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

        if not _scheduler.depth:
            _scheduler.owner = None

        try:
            _scheduler.flush()
        except BaseException as exc:
            if body_error is not None:
                raise BaseExceptionGroup(
                    "batch body and observers failed", [body_error, exc]
                ) from None

            raise


class Signal[T]:
    def __init__(self, value: T, *, equal: Callable[[T, T], bool] | None = None) -> None:
        self._value = value
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

        return self._value

    __call__ = get

    def publish(self, value: T) -> None:
        if self.initialized and self._equal(value, self._value):
            return

        self.initialized = True
        self._value = value

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
                    fn(value)
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
