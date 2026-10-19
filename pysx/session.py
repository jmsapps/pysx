"""Session graphs and serialized reactive mutation ownership."""

from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

from . import tracing
from .async_runtime import MutationRuntime, active_runtime
from .errors import ErrorInfo, FailureCapture, failure_context, report_error
from .events import decode_event
from .forms import PayloadError
from .lifecycle import begin_restore, end_restore, resources_preparing
from .reactive import Effect, batch, write_guard
from .render import Fragment, Watcher, render
from .request import RequestContext, browser_id, request_context
from .routing import RouteHost, route_host
from .styles import style_context
from .transport import TransportState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator
    from string.templatelib import Template
    from typing import Literal

    from .wire import Op, ResultMessage


class Session:
    """Holds one client's signal graph and the ops it has produced."""

    durability: Literal["volatile", "durable"] = "volatile"

    def __init__(
        self,
        app_fn: Callable[[], Template | Fragment],
        *,
        location: str = "/",
        browser: str = "",
        development: bool = False,
        request: RequestContext | None = None,
        mutation_capacity: int = 64,
        task_capacity: int = 1024,
        outbox_bytes: int = 2097152,
    ) -> None:
        self.request = request or RequestContext(path=location)
        self.location = location
        self.browser = browser or browser_id(self.request)
        self.closed = False
        self.revoked = False
        self.recovery_failed = False
        self.runtime = MutationRuntime(capacity=mutation_capacity, tasks=task_capacity)
        self.runtime.development = development
        self.changed = asyncio.Event()
        self.runtime.finished = self._finished
        self.runtime.failed = self.background_error
        self.pending: list[Op] = []
        self.outbox_bytes = outbox_bytes
        self.pressured = False
        self.detached = False
        self._awaitables: list[Awaitable[object]] = []
        self._callback_sources: list[Awaitable[object]] = []
        self._live = False
        self._failed_callback: Exception | None = None
        self.development = development
        self.trace = (
            tracing.Trace()
            if development and tracing.enabled and not resources_preparing()
            else None
        )
        self.routes = RouteHost(location, base_path=self.request.base_path)
        route_token = route_host.set(self.routes)
        request_token = request_context.set(self.request)
        runtime_token = active_runtime.set(self.runtime)
        guard_token = write_guard.set(self.runtime.check_write)
        trace_token = tracing.active.set(self.trace) if tracing.enabled else None
        restore_token = (
            begin_restore(self.browser, self.request.principal, location) if development else None
        )

        try:
            self.rendered = render(app_fn, development=development)
        except BaseException:
            self.runtime.close()

            raise
        finally:
            route_host.reset(route_token)
            request_context.reset(request_token)
            active_runtime.reset(runtime_token)
            write_guard.reset(guard_token)

            if trace_token is not None:
                tracing.active.reset(trace_token)

            if restore_token is not None:
                end_restore(restore_token)
        # The first run of each effect registers its subscription; watchers were
        # seeded with the rendered value, so it produces ops only where setup
        # wrote a signal after its hole had already been emitted.
        self.effects: list[Effect] = []
        self.transport = TransportState(self.rendered.body)
        self.rendered.context = self.context
        self.runtime.context = self.context
        self.rendered.sink = self._owned_ops

        try:
            for watcher in self.rendered.watchers:
                self.effects.append(self._watch(watcher))
            self.rendered.flush()
        except BaseException as error:
            try:
                self.dispose()
            except Exception as cleanup_error:
                error.add_note(f"session cleanup also failed: {cleanup_error}")

            raise
        self._live = True
        self.routes.live = True
        self.rendered.failures.clear()

    def checkpoint(self) -> None:
        """Volatile sessions retain no durable application state."""

    def _watch(self, watcher: Watcher) -> Effect:
        token = failure_context.set(watcher.failure)
        trace_token = tracing.active.set(self.trace) if tracing.enabled else None

        try:
            return Effect(lambda: self._collect(watcher), phase="render")
        finally:
            failure_context.reset(token)

            if trace_token is not None:
                tracing.active.reset(trace_token)

    def _owned_ops(self, ops: list[Op]) -> None:
        self._sync_styles()
        self.transport.apply(ops)
        self._queue(ops)
        self.changed.set()

    def _finished(self) -> None:
        if self.detached:
            self.pending.clear()
            self.routes.commands.clear()
            self.rendered.failures.clear()
        self.changed.set()

    def _queue(self, ops: list[Op]) -> None:
        if self.detached:
            self.pending.clear()
            self.routes.commands.clear()
            self.rendered.failures.clear()

            return

        if self.pressured:
            return

        if len(json.dumps([*self.pending, *ops]).encode()) > self.outbox_bytes:
            self.pressured = True
            self.pending.clear()
        else:
            self.pending.extend(ops)

            if self.trace is not None:
                self.trace.record("patch.queue", count=len(ops))
        self.changed.set()

    @contextmanager
    def context(self) -> Generator[None]:
        """Session-owned styles and route host, for every path that runs user code."""
        style_token = style_context.set(self.rendered.styles)
        route_token = route_host.set(self.routes)
        request_token = request_context.set(self.request)
        runtime_token = active_runtime.set(self.runtime)
        guard_token = write_guard.set(self.runtime.check_write)
        trace_token = tracing.active.set(self.trace) if tracing.enabled else None

        try:
            yield
        finally:
            route_host.reset(route_token)
            request_context.reset(request_token)
            active_runtime.reset(runtime_token)
            write_guard.reset(guard_token)
            style_context.reset(style_token)

            if trace_token is not None:
                tracing.active.reset(trace_token)

    def _collect(self, watcher: Watcher) -> None:
        with self.context():
            if self.trace is not None:
                self.trace.record("render", id(watcher))
            ops = self.rendered.refresh(watcher)
        self._sync_styles()

        if ops:
            self.transport.apply(ops)
            self._queue(ops)
            self.changed.set()

    def _sync_styles(self) -> None:
        css = self.rendered.styles.snapshot()

        if css != self.rendered.css:
            self.rendered.css = css

            if self._live and not self.detached:
                self.pending[:] = [op for op in self.pending if op["op"] != "css"]
                self.pending.insert(0, {"op": "css", "v": css})
                self._queue([])
                self.changed.set()

    def background_error(self, error: Exception) -> None:
        info = ErrorInfo(None, "async")
        report_error(info, error, development=self.development)

        if not self.detached:
            self.rendered.failures.append(info)
        self.changed.set()

    def _invoke(
        self,
        callback: Callable[[object], object],
        value: object,
        capture: FailureCapture | None = None,
    ) -> None:
        token = failure_context.set(capture)

        try:
            with self.context():
                result = callback(value)
        except Exception as error:
            if capture is None or not capture.handle(error, "handler"):
                self._failed_callback = error

                raise

            return
        finally:
            failure_context.reset(token)
        self._sync_styles()

        if inspect.isawaitable(result):
            self._callback_sources.append(result)

            async def owned_callback() -> object:
                token = failure_context.set(capture)

                try:
                    return await result
                except Exception as error:
                    if capture is None or not capture.handle(error, "async"):
                        self._failed_callback = error

                        raise

                    return None
                finally:
                    failure_context.reset(token)
                    self._callback_sources[:] = [
                        item for item in self._callback_sources if item is not result
                    ]

            self._awaitables.append(owned_callback())

    async def dispatch_async(
        self,
        handler_id: str,
        value: object,
        revision: int | None = None,
        *,
        after: str | None = None,
        edits: list[tuple[str, object, int | None]] | None = None,
        event: object = None,
        navigation: bool = False,
    ) -> list[Op]:
        if self.runtime.owner is not asyncio.current_task():
            async with self.runtime.turn():
                return await self.dispatch_async(
                    handler_id,
                    value,
                    revision,
                    after=after,
                    edits=edits,
                    event=event,
                    navigation=navigation,
                )
        self.routes.intercepting = navigation

        try:
            with self.context():
                ops = self.dispatch(
                    handler_id, value, revision, after=after, edits=edits, event=event
                )
                self.pending.extend(ops)

                while self._awaitables:
                    await self._awaitables.pop(0)
                    self._sync_styles()
        except BaseException:
            self.cancel_callbacks()

            raise
        finally:
            self.routes.intercepting = False
        ops = self.pending
        self.pending = []

        return ops

    def dispatch(
        self,
        handler_id: str,
        value: object,
        revision: int | None = None,
        *,
        after: str | None = None,
        edits: list[tuple[str, object, int | None]] | None = None,
        event: object = None,
    ) -> list[Op]:
        fn = self.rendered.handlers.get(handler_id)
        listener = self.rendered.dom.listeners.get(handler_id)
        capture = self.rendered.handler_failures.get(handler_id)

        if listener is not None:
            fn = cast("Callable[[object], object]", listener[0].callback)
            capture = self.rendered.dom.listener_failures.get(handler_id)

        if fn is None and not edits:
            return []
        typed = self.rendered.event_handlers.get(handler_id)

        if listener is not None:
            typed = (listener[0], listener[1])
        following = self.rendered.event_handlers.get(after or "")
        snapshot = decode_event(event, handler_id, typed[1]) if typed else None
        after_snapshot = decode_event(event, after or "", following[1]) if following else None
        updates = list(edits or [])
        binding = self.rendered.bindings.get(handler_id)

        if binding is not None:
            updates.append((handler_id, value, revision))

        for hid, payload, _rev in updates:
            target = self.rendered.bindings.get(hid)

            if target is not None:
                target.validate(payload)
        self.pending.clear()
        with batch():
            for hid, payload, _rev in edits or []:
                target = self.rendered.bindings.get(hid)

                if target is not None:
                    target.set(payload)

            if fn is not None:
                if typed is not None and snapshot is not None:
                    self._invoke(
                        cast("Callable[[object], object]", typed[0].callback), snapshot, capture
                    )
                else:
                    self._invoke(fn, value, capture)

            if after is not None and after != handler_id:
                callback = self.rendered.handlers.get(after)

                if callback is not None:
                    if following is not None and after_snapshot is not None:
                        self._invoke(
                            cast("Callable[[object], object]", following[0].callback),
                            after_snapshot,
                            self.rendered.handler_failures.get(after),
                        )
                    else:
                        self._invoke(callback, value, self.rendered.handler_failures.get(after))
        ops = self.pending
        self.pending = []

        for hid, payload, rev in updates:
            target = self.rendered.bindings.get(hid)

            if target is None:
                continue
            equal = target.signal() == payload
            own = [
                op
                for op in ops
                if (op["op"] == "attr" or op["op"] == "prop")
                and op["id"] == target.element
                and op["name"] == target.name
            ]

            if equal:
                ops = [op for op in ops if op not in own]
            else:
                correction = target.op()

                if rev is not None and (correction["op"] == "attr" or correction["op"] == "prop"):
                    correction["rev"] = rev
                ops = [op for op in ops if op not in own]
                ops.append(correction)

        return ops

    async def dispatch_result_async(
        self,
        handler_id: str,
        value: object,
        revision: int | None = None,
        *,
        after: str | None = None,
        edits: list[tuple[str, object, int | None]] | None = None,
        event: object = None,
        navigation: bool = False,
    ) -> ResultMessage:
        """Commit actual-state ops and acknowledge a recoverable dispatch outcome."""

        if self.runtime.owner is not asyncio.current_task():
            async with self.runtime.turn():
                return await self.dispatch_result_async(
                    handler_id,
                    value,
                    revision,
                    after=after,
                    edits=edits,
                    event=event,
                    navigation=navigation,
                )
        self._failed_callback = None
        status: Literal["success", "error", "invalid", "stale"] = "success"
        info: ErrorInfo | None = None
        prior, self.pending = self.pending, []
        stale = (
            handler_id not in self.rendered.handlers
            and handler_id not in self.rendered.dom.listeners
        )

        try:
            if stale and not edits:
                ops, self.pending = self.pending, []
            else:
                ops = await self.dispatch_async(
                    handler_id,
                    value,
                    revision,
                    after=after,
                    edits=edits,
                    event=event,
                    navigation=navigation,
                )

            if stale:
                status = "stale"
        except PayloadError as error:
            if self._callback_failed(error):
                status = "error"
                info = ErrorInfo(None, "handler")
                report_error(info, error, development=self.development)
            else:
                status = "invalid"
            ops, self.pending = self.pending, []
        except Exception as error:
            status = "error"
            info = ErrorInfo(None, "handler")
            report_error(info, error, development=self.development)
            ops, self.pending = self.pending, []
        except BaseException:
            self.pending = prior + self.pending

            raise
        finally:
            self.cancel_callbacks()
            self.routes.intercepting = False
            self._failed_callback = None

        if self.rendered.failures:
            status = "error"
            info = self.rendered.failures[0]
        self.rendered.failures.clear()
        ops = prior + ops

        if status in ("error", "invalid"):
            updates = [*(edits or []), (handler_id, value, revision)]

            for hid, _payload, rev in updates:
                binding = self.rendered.bindings.get(hid)

                if binding is not None:
                    correction = binding.op()

                    if rev is not None and (
                        correction["op"] == "attr" or correction["op"] == "prop"
                    ):
                        correction["rev"] = rev
                    ops.append(correction)
        result: ResultMessage = {"t": "result", "h": handler_id, "status": status, "ops": ops}

        if info is not None:
            result["error"] = info.wire()

        return result

    def cancel_callbacks(self) -> None:
        for awaitable in self._awaitables:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
        self._awaitables.clear()

        for awaitable in self._callback_sources:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
        self._callback_sources.clear()

    @property
    def pending_callbacks(self) -> int:
        return len(self._callback_sources)

    def _callback_failed(self, error: Exception) -> bool:
        return self._failed_callback is error

    def dispose(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.runtime.owner = None

        try:
            with self.context():
                self._dispose()
        finally:
            self.runtime.close()

    def _dispose(self) -> None:
        self.cancel_callbacks()
        errors: list[Exception] = []

        for eff in self.effects:
            try:
                eff.dispose()
            except Exception as error:
                errors.append(error)
        self.effects.clear()
        self.pending.clear()

        try:
            self.rendered.dispose()
        except Exception as error:
            errors.append(error)

        if errors:
            raise ExceptionGroup("session cleanup failed", errors)


async def close_session(
    session: Session, worker: asyncio.Task[None] | None, error: BaseException | None
) -> None:
    """Dispose a connection's session without masking the error that ended it."""

    if worker is not None:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    try:
        session.dispose()
    except Exception as cleanup_error:
        if error is None:
            raise
        error.add_note(f"session cleanup also failed: {cleanup_error}")
