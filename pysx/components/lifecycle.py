"""Render-owned component state and exception-safe resource lifetimes."""

from __future__ import annotations

import ast
import asyncio
import math
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
from inspect import currentframe
from linecache import getlines
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from pysx.reactive import reactive
from pysx.reactive.reactive import Effect, Signal, untracked
from pysx.runtime.async_runtime import active_runtime

from .errors import FailureCapture, failure_context

_preparing: ContextVar[bool] = ContextVar("pysx_preparing_resources", default=False)
_restoring: ContextVar[dict[str, dict[str, object]] | None] = ContextVar(
    "pysx_restoring_local_state", default=None
)

if TYPE_CHECKING:
    from collections.abc import Coroutine, Generator


def resources_preparing() -> bool:
    """Whether this construction turn is an isolated development preflight."""

    return _preparing.get()


@contextmanager
def stage_resources() -> Generator[None]:
    """Construct disposable probe graphs without activating owned resources."""
    token = _preparing.set(True)

    try:
        yield
    finally:
        _preparing.reset(token)


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
    slots: dict[str, Signal[object]] = field(default_factory=dict[str, Signal[object]])
    marks: dict[str, str] = field(default_factory=dict[str, str])
    kinds: dict[str, str] = field(default_factory=dict[str, str])
    sites: dict[str, int] = field(default_factory=dict[str, int])
    reset: dict[str, str] = field(default_factory=dict[str, str])
    preserved: set[str] = field(default_factory=set[str])
    resources: set[str] = field(default_factory=set[str])
    cleanups: list[Callable[[], object]] = field(default_factory=list[Callable[[], object]])
    mounts: list[Callable[[], object]] = field(default_factory=list[Callable[[], object]])
    failure: FailureCapture | None = field(default_factory=failure_context.get)

    def close(self) -> list[Exception]:
        if not self.alive:
            return []
        self.alive = False
        errors: list[Exception] = []
        callbacks, self.cleanups = self.cleanups, []
        self.mounts.clear()
        self.values.clear()
        self.slots.clear()
        self.marks.clear()
        self.kinds.clear()
        self.sites.clear()
        self.reset.clear()
        self.preserved.clear()
        self.resources.clear()

        for callback in reversed(callbacks):
            try:
                callback()
            except Exception as error:
                if self.failure is None or not self.failure.handle(error, "cleanup"):
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


def active_scope() -> Scope | None:
    owner = _scope.get()

    return owner if owner is not None and owner.alive else None


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
        owner.sites.clear()
        owner.slots.clear()
        owner.marks.clear()
        owner.kinds.clear()
        owner.reset.clear()
        owner.preserved.clear()
        token = _scope.set(owner)
        failure_token = failure_context.set(owner.failure)

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
            failure_context.reset(failure_token)
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
            failure_token = failure_context.set(owner.failure)

            try:
                for callback in callbacks:
                    if not owner.alive:
                        break

                    try:
                        callback()
                    except Exception as error:
                        if owner.failure is None or not owner.failure.handle(error, "mount"):
                            errors.append(error)
            finally:
                failure_context.reset(failure_token)
                owner.mounting = False
                _scope.reset(token)

        if errors:
            raise ExceptionGroup("component mount failed", errors)

    def close(self) -> None:
        self.closed = True
        self.release("")


@lru_cache(maxsize=128)
def _site_marks(filename: str, source: str) -> dict[int, str]:
    from pysx.compiler.compiler import analyze
    from pysx.compiler.scope import ScopeIndex

    compilation = analyze(source, filename)

    if compilation.tree is None:
        return {}
    scopes = ScopeIndex(compilation.tree, package=compilation.resolver.package)
    sites: dict[int, list[str]] = {}
    repeated = {
        child
        for node in ast.walk(compilation.tree)
        if isinstance(
            node,
            (
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.GeneratorExp,
            ),
        )
        for child in ast.walk(node)
    }

    for node in ast.walk(compilation.tree):
        if not isinstance(node, ast.Call):
            continue
        scope = scopes.nodes.get(node)

        if scope is None or scope.identity(node.func) not in ("pysx.signal", "pysx.local_state"):
            continue
        mark = "" if node in repeated else sha256(ast.dump(node).encode()).hexdigest()[:16]

        for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
            sites.setdefault(line, []).append(mark)

    return {line: marks[0] if len(marks) == 1 else "" for line, marks in sites.items()}


def _fingerprint(filename: str, line: int) -> str:
    return _site_marks(filename, "".join(getlines(filename))).get(line, "")


def local_value_kind(value: object, depth: int = 0) -> str:
    """Describe exact lossless JSON types; reject opaque or unbounded shapes."""

    if depth > 32:
        raise ValueError("value nesting limit")
    kind = type(value)

    if kind in (type(None), bool, int, str):
        return kind.__name__

    if kind is float and math.isfinite(cast("float", value)):
        return "float"

    if kind is list:
        children = cast("list[object]", value)

        return "list[" + ",".join(sorted({local_value_kind(v, depth + 1) for v in children})) + "]"

    if kind is dict:
        entries = cast("dict[object, object]", value)

        if all(type(key) is str for key in entries):
            kinds = {local_value_kind(v, depth + 1) for v in entries.values()}

            return "dict[" + ",".join(sorted(kinds)) + "]"

    raise ValueError("unsupported value type")


def _record(created: Signal[object]) -> None:
    owner = active_scope()

    if owner is None:
        return
    frame = currentframe()

    try:
        caller = frame.f_back.f_back if frame is not None and frame.f_back is not None else None

        if caller is None:
            return
        caller_file = caller.f_code.co_filename
        caller_line = caller.f_lineno
        site = f"{caller_file}:{caller_line}"
    finally:
        del frame
    index = owner.sites.get(site, 0)
    owner.sites[site] = index + 1

    slot = f"{site}#{index}"
    mark = _fingerprint(caller_file, caller_line)
    _restore_slot(owner, slot, created, mark)


def _restore_slot(owner: Scope, slot: str, created: Signal[object], mark: str) -> None:
    if slot not in owner.slots and len(owner.slots) >= 1024:
        raise RuntimeError("component resource limit exceeded")
    owner.slots[slot] = created
    owner.marks[slot] = mark

    try:
        owner.kinds[slot] = local_value_kind(created.published())
    except ValueError:
        owner.kinds[slot] = "unsupported"
    retained = _restoring.get()

    if retained is None:
        return
    held = retained.get(owner.path, {})
    entry = held.get(slot)

    if entry is None:
        owner.reset[slot] = "absent"

        return

    pair = cast("list[object]", entry) if isinstance(entry, list) else []

    if len(pair) != 3 or not isinstance(pair[2], dict):
        owner.reset[slot] = "malformed"

        return

    metadata = cast("dict[str, object]", pair[2])

    if isinstance(metadata.get("reason"), str):
        owner.reset[slot] = cast("str", metadata["reason"])

        return

    if not mark or not pair[1]:
        owner.reset[slot] = "ambiguous call site"

        return

    if pair[1] != mark:
        owner.reset[slot] = "initializer changed"

        return

    if metadata.get("kind") != owner.kinds[slot]:
        owner.reset[slot] = "initializer type changed"

        return

    try:
        restored_kind = local_value_kind(pair[0])
    except ValueError as error:
        owner.reset[slot] = str(error)

        return

    initial_kind = owner.kinds[slot]

    if restored_kind != initial_kind and initial_kind not in ("list[]", "dict[]"):
        owner.reset[slot] = "value type changed"

        return
    created.publish(pair[0])
    owner.preserved.add(slot)


def capture_local_state(*, enable: bool) -> None:
    """Record component-local signal identities for development replacement."""
    reactive.created = _record if enable else None


@contextmanager
def restoring_local_state(values: dict[str, dict[str, object]] | None) -> Generator[None]:
    """Restore one authorized handoff during construction of its replacement graph."""
    token = _restoring.set(values)

    try:
        yield
    finally:
        _restoring.reset(token)


def local_state[T](key: str, initial: T) -> Signal[T]:
    """Reuse a named signal for the lifetime of the current component identity."""
    owner = current_scope()

    if key not in owner.values:
        if len(owner.values) >= 1024:
            raise RuntimeError("component resource limit exceeded")
        owner.values[key] = _OwnedSignal(initial, owner)

    created = cast("Signal[T]", owner.values[key])

    if reactive.created is not None:
        frame = currentframe()

        try:
            caller = frame.f_back if frame is not None else None
            mark = _fingerprint(caller.f_code.co_filename, caller.f_lineno) if caller else ""
        finally:
            del frame
        _restore_slot(owner, f"named:{key}#0", cast("Signal[object]", created), mark)

    return created


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

        if not _preparing.get():
            cleanup = subscribe()
            owner.cleanups.append(cleanup)
        owner.resources.add(resource_key)


def own_effect(key: str, callback: Callable[[], object]) -> None:
    own_subscription(f"effect:{key}", lambda: Effect(callback).dispose)


def own_timer(key: str, delay: float, callback: Callable[[], object]) -> None:
    owner = current_scope()
    runtime = active_runtime.get()

    def invoke() -> None:
        if owner.alive:
            token = failure_context.set(owner.failure)

            try:
                callback()
            except Exception as error:
                if owner.failure is None or not owner.failure.handle(error, "async"):
                    raise
            finally:
                failure_context.reset(token)

    def fire() -> None:
        if runtime is None:
            invoke()
        elif owner.alive and not runtime.closed:
            future = runtime.schedule(invoke)
            future.add_done_callback(lambda done: None if done.cancelled() else done.exception())

    own_subscription(
        f"timer:{key}", lambda: asyncio.get_running_loop().call_later(delay, fire).cancel
    )


def own_task(key: str, factory: Callable[[], Coroutine[object, object, object]]) -> None:
    owner = current_scope()
    runtime = active_runtime.get()

    def start() -> Callable[[], object]:
        with untracked():
            task = (
                asyncio.get_running_loop().create_task(factory())
                if runtime is None
                else runtime.spawn(factory)
            )

        def finished(done: asyncio.Task[object]) -> None:
            if done.cancelled():
                return
            error = done.exception()

            if error is None:
                return

            def handle() -> None:
                handled = (
                    isinstance(error, Exception)
                    and owner.alive
                    and owner.failure is not None
                    and owner.failure.handle(error, "async")
                )

                if not handled:
                    done.get_loop().call_exception_handler(
                        {"message": "owned task failed", "exception": error, "task": done}
                    )

            if runtime is not None and not runtime.closed:
                future = runtime.schedule(handle)
                future.add_done_callback(
                    lambda result: None if result.cancelled() else result.exception()
                )
            else:
                handle()

        task.add_done_callback(finished)

        return task.cancel

    own_subscription(f"task:{key}", start)
