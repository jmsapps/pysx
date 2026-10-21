"""Scope-owned asynchronous values with cancellation and latest-result guards."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from pysx.components.errors import ErrorInfo, report_error
from pysx.components.lifecycle import current_scope, own_subscription, resources_preparing
from pysx.hosting.security import require_access
from pysx.reactive.reactive import Signal, signal

from .async_runtime import active_runtime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Literal

    from pysx.components.lifecycle import Scope
    from pysx.reactive.reactive import Readable

    from .async_runtime import MutationRuntime


@dataclass(frozen=True)
class ResourceState[T]:
    status: Literal["pending", "success", "error", "cancelled"]
    value: T | None = None
    error: ErrorInfo | None = None


class Resource[T]:
    def __init__(
        self,
        loader: Callable[[], Awaitable[T]],
        owner: Scope,
        runtime: MutationRuntime,
        key: str = "",
    ) -> None:
        self.loader = loader
        self.key = key
        self.owner = owner
        self.runtime = runtime
        self.version = 0
        self.task: asyncio.Task[None] | None = None
        self._state: Signal[ResourceState[T]] = signal(ResourceState("pending"))
        self.state: Readable[ResourceState[T]] = self._state
        self.reload()

    def __call__(self) -> ResourceState[T]:
        return self.state()

    def _live(self, version: int) -> bool:
        return self.owner.alive and not self.runtime.closed and self.version == version

    def reload(self) -> None:
        if resources_preparing():
            return

        if not self.owner.alive or self.runtime.closed:
            raise RuntimeError("resource owner has been disposed")
        self.version += 1
        version = self.version

        if self.task is not None:
            self.task.cancel()

        async def load() -> None:
            try:
                with self.runtime.context():
                    require_access("resource", self.key)
                await self.runtime.update(lambda: self._publish(version, ResourceState("pending")))
                value = await self.loader()
                await self.runtime.update(
                    lambda: self._publish(version, ResourceState("success", value))
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if not self._live(version):
                    return
                info = ErrorInfo(self.owner.token, "async")

                def failed(cause: Exception = error) -> None:
                    if not self._live(version):
                        return
                    self._state.set(ResourceState("error", error=info))

                    if self.owner.failure is None or not self.owner.failure.handle(cause, "async"):
                        report_error(info, cause, development=self.runtime.development)

                await self.runtime.update(failed)

        self.task = self.runtime.spawn(load)

    def _publish(self, version: int, state: ResourceState[T]) -> None:
        if self._live(version):
            if state.status == "success":
                require_access("resource", self.key)
            self._state.set(state)

    def cancel(self) -> None:
        self.version += 1
        version = self.version

        if self.task is not None:
            self.task.cancel()

        if self.owner.alive and not self.runtime.closed:
            future = self.runtime.schedule(
                lambda: self._publish(version, ResourceState("cancelled"))
            )
            future.add_done_callback(
                lambda result: None if result.cancelled() else result.exception()
            )


def resource[T](key: str, loader: Callable[[], Awaitable[T]]) -> Resource[T]:
    owner = current_scope()
    runtime = active_runtime.get()

    if runtime is None:
        raise RuntimeError("resources require an application session")
    name = f"async-resource:{key}"

    if name not in owner.values:
        if len(owner.resources) >= 1024:
            raise RuntimeError("component resource limit exceeded")
        value = Resource(loader, owner, runtime, key)
        own_subscription(name, lambda: value.cancel)
        owner.values[name] = value

    found = cast("Resource[T]", owner.values[name])
    found.loader = loader

    return found
