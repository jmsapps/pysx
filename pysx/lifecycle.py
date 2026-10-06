"""Render-owned component state and exception-safe resource lifetimes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from .reactive import Effect, Signal

if TYPE_CHECKING:
    from collections.abc import Coroutine, Generator


@dataclass
class Scope:
    path: str
    identity: object
    token: str = field(default_factory=lambda: uuid4().hex)
    alive: bool = True
    ready: bool = False
    mounted: bool = False
    mounting: bool = False
    values: dict[str, object] = field(default_factory=dict[str, object])
    resources: set[str] = field(default_factory=set[str])
    cleanups: list[Callable[[], object]] = field(default_factory=list[Callable[[], object]])
    mounts: list[Callable[[], object]] = field(default_factory=list[Callable[[], object]])

    def close(self) -> list[Exception]:
        if not self.alive:
            return []
        self.alive = False
        errors: list[Exception] = []
        callbacks, self.cleanups = self.cleanups, []
        self.mounts.clear()
        self.values.clear()
        self.resources.clear()

        for callback in reversed(callbacks):
            try:
                callback()
            except Exception as error:
                errors.append(error)

        return errors


_scope: ContextVar[Scope | None] = ContextVar("pysx_component_scope", default=None)


class _OwnedSignal[T](Signal[T]):
    def __init__(self, value: T, owner: Scope) -> None:
        super().__init__(value)
        self.owner = owner

    def set(self, value: T) -> None:
        if not self.owner.alive:
            raise RuntimeError("component state owner has been disposed")
        super().set(value)


def current_scope() -> Scope:
    owner = _scope.get()

    if owner is None or not owner.alive:
        raise RuntimeError("component hooks require an active component owner")

    return owner


class Scopes:
    def __init__(self) -> None:
        self.owners: dict[str, Scope] = {}
        self.closed = False
        self.traversals: list[set[str]] = []

    @contextmanager
    def enter(self, path: str, identity: object) -> Generator[Scope]:
        if self.closed:
            raise RuntimeError("component scopes are closed")
        owner = self.owners.get(path)

        if owner is not None and owner.identity != identity:
            self.release(path)
            owner = None

        if owner is None:
            owner = Scope(path, identity)
            self.owners[path] = owner

        for seen in self.traversals:
            seen.add(path)
        token = _scope.set(owner)

        try:
            yield owner
            owner.ready = True
        except BaseException as error:
            try:
                self.release(path)
            except Exception as cleanup_error:
                error.add_note(f"component cleanup also failed: {cleanup_error}")

            raise
        finally:
            _scope.reset(token)

    def release(self, prefix: str) -> None:
        errors: list[Exception] = []
        paths = sorted(
            (path for path in self.owners if path.startswith(prefix)), key=len, reverse=True
        )

        for path in paths:
            errors.extend(self.owners.pop(path).close())

        if errors:
            raise ExceptionGroup("component cleanup failed", errors)

    @contextmanager
    def reconcile(self, prefix: str) -> Generator[None]:
        seen: set[str] = set()
        self.traversals.append(seen)

        try:
            yield
        finally:
            self.traversals.pop()
        self.retain(prefix, seen)

    def retain(self, prefix: str, seen: set[str]) -> None:
        errors: list[Exception] = []

        for path in tuple(self.owners):
            if path.startswith(prefix) and path not in seen:
                try:
                    self.release(path)
                except Exception as error:
                    errors.append(error)

        if errors:
            raise ExceptionGroup("component cleanup failed", errors)

    def pending_mounts(self) -> list[str]:
        return [
            owner.token
            for owner in self.owners.values()
            if owner.alive and not owner.mounted and owner.mounts
        ]

    def acknowledge(self, tokens: list[str]) -> None:
        accepted = set(tokens)
        errors: list[Exception] = []

        for owner in tuple(self.owners.values()):
            if not owner.alive or owner.mounted or owner.token not in accepted:
                continue
            owner.mounted = True
            owner.mounting = True
            callbacks, owner.mounts = owner.mounts, []
            token = _scope.set(owner)

            try:
                for callback in callbacks:
                    if not owner.alive:
                        break

                    try:
                        callback()
                    except Exception as error:
                        errors.append(error)
            finally:
                owner.mounting = False
                _scope.reset(token)

        if errors:
            raise ExceptionGroup("component mount failed", errors)

    def close(self) -> None:
        self.closed = True
        self.release("")


def local_state[T](key: str, initial: T) -> Signal[T]:
    """Reuse a named signal for the lifetime of the current component identity."""
    owner = current_scope()

    if key not in owner.values:
        if len(owner.values) >= 1024:
            raise RuntimeError("component resource limit exceeded")
        owner.values[key] = _OwnedSignal(initial, owner)

    return cast("Signal[T]", owner.values[key])


def on_setup(callback: Callable[[], object]) -> None:
    """Run server setup once; register its resource cleanup with on_cleanup."""
    if not current_scope().ready:
        callback()


def on_cleanup(callback: Callable[[], object]) -> None:
    owner = current_scope()

    if not owner.ready or owner.mounting:
        if len(owner.cleanups) >= 1024:
            raise RuntimeError("component cleanup limit exceeded")
        owner.cleanups.append(callback)


def on_mount(callback: Callable[[], object]) -> None:
    """Run once after the browser acknowledges this owner's DOM commit."""
    owner = current_scope()

    if not owner.ready:
        if len(owner.mounts) >= 1024:
            raise RuntimeError("component mount limit exceeded")
        owner.mounts.append(callback)


def own_subscription(key: str, subscribe: Callable[[], Callable[[], object]]) -> None:
    owner = current_scope()
    resource_key = f"subscription:{key}"

    if resource_key not in owner.resources:
        if len(owner.resources) >= 1024:
            raise RuntimeError("component resource limit exceeded")
        cleanup = subscribe()
        owner.cleanups.append(cleanup)
        owner.resources.add(resource_key)


def own_effect(key: str, callback: Callable[[], object]) -> None:
    own_subscription(f"effect:{key}", lambda: Effect(callback).dispose)


def own_timer(key: str, delay: float, callback: Callable[[], object]) -> None:
    owner = current_scope()

    def invoke() -> None:
        if owner.alive:
            callback()

    own_subscription(
        f"timer:{key}", lambda: asyncio.get_running_loop().call_later(delay, invoke).cancel
    )


def own_task(key: str, factory: Callable[[], Coroutine[object, object, object]]) -> None:
    def start() -> Callable[[], object]:
        task = asyncio.get_running_loop().create_task(factory())

        return task.cancel

    own_subscription(f"task:{key}", start)
