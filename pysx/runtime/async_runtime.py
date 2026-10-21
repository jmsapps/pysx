"""Serialized synchronous mutation turns around independently awaited work."""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from contextvars import Context, ContextVar, copy_context
from threading import get_ident
from typing import TYPE_CHECKING, cast

from pysx.reactive.reactive import batch, untracked

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Coroutine
    from contextlib import AbstractContextManager


active_runtime: ContextVar[MutationRuntime | None] = ContextVar("pysx_async_runtime", default=None)


class MutationRuntime:
    def __init__(self, *, capacity: int = 64, tasks: int = 1024) -> None:
        if capacity < 1 or tasks < 1:
            raise ValueError("mutation and task capacity must be positive")
        self.capacity = capacity
        self.task_capacity = tasks
        self.development = False
        self.queue: asyncio.Queue[tuple[Callable[[], object], asyncio.Future[object], Context]] = (
            asyncio.Queue(capacity)
        )
        self.lock = asyncio.Lock()
        self.owner: asyncio.Task[object] | None = None
        self.thread = get_ident()
        self.closed = False
        self.worker: asyncio.Task[None] | None = None
        self.tasks: set[asyncio.Task[object]] = set()
        self.context: Callable[[], AbstractContextManager[None]] = untracked
        self.finished: Callable[[], None] = lambda: None
        self.failed: Callable[[Exception], None] = lambda _error: None
        self.admit: Callable[[], None] = lambda: None

    def check_write(self) -> None:
        if self.closed:
            raise RuntimeError("session has been disposed")

        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None

        if get_ident() != self.thread or (self.owner is not None and task is not self.owner):
            raise RuntimeError("session writes require a serialized mutation turn")

    @asynccontextmanager
    async def turn(self) -> AsyncGenerator[None]:
        async with self.lock:
            self.admit()
            self.check_write()
            self.owner = asyncio.current_task()

            try:
                with self.context():
                    yield
            finally:
                self.owner = None
                self.finished()

    def schedule[T](self, callback: Callable[[], T]) -> asyncio.Future[T]:
        if self.closed:
            raise RuntimeError("session has been disposed")
        loop = asyncio.get_running_loop()
        result: asyncio.Future[object] = loop.create_future()
        with untracked():
            context = copy_context()
        self.queue.put_nowait((callback, result, context))

        if self.worker is None:
            with untracked():
                self.worker = loop.create_task(self._drain(), context=Context())

        return cast("asyncio.Future[T]", result)

    async def update[T](self, callback: Callable[[], T]) -> T:
        if self.owner is asyncio.current_task():
            self.check_write()
            with batch():
                return self._call(callback)

        return await self.schedule(callback)

    @staticmethod
    def _call[T](callback: Callable[[], T]) -> T:
        result = callback()

        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()

            raise TypeError("mutation callbacks must be synchronous")

        return result

    async def _drain(self) -> None:
        while True:
            callback, result, context = await self.queue.get()

            try:
                async with self.turn():
                    if result.cancelled():
                        continue
                    with batch():
                        def invoke(work: Callable[[], object] = callback) -> object:
                            with self.context(), untracked():
                                return self._call(work)

                        value = context.run(invoke)

                if not result.done():
                    result.set_result(value)
            except Exception as error:
                self.failed(error)

                if not result.done():
                    result.set_exception(error)
            except asyncio.CancelledError:
                result.cancel()

                raise
            finally:
                self.queue.task_done()

    def spawn[T](self, factory: Callable[[], Coroutine[object, object, T]]) -> asyncio.Task[T]:
        if self.closed:
            raise RuntimeError("session has been disposed")

        if len(self.tasks) >= self.task_capacity:
            raise RuntimeError("session task capacity reached")
        with untracked():
            task = asyncio.get_running_loop().create_task(factory())
        erased = cast("asyncio.Task[object]", task)
        self.tasks.add(erased)
        task.add_done_callback(lambda _done: self.tasks.discard(erased))

        return task

    def close(self) -> None:
        self.closed = True

        if self.worker is not None:
            self.worker.cancel()

        for task in self.tasks:
            task.cancel()

        while not self.queue.empty():
            _callback, result, _context = self.queue.get_nowait()
            result.cancel()
            self.queue.task_done()


async def push[T](callback: Callable[[], T]) -> T:
    """Commit one synchronous batch from session-owned async work."""
    runtime = active_runtime.get()

    if runtime is None:
        raise RuntimeError("push requires an application session")

    return await runtime.update(callback)
