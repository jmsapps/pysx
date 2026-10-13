"""Serialized async work has bounded admission and owned latest-result semantics."""

import asyncio
from typing import TYPE_CHECKING

import pytest

from pysx import (
    ErrorBoundary,
    Fragment,
    Resource,
    derived,
    each,
    effect,
    native,
    own_effect,
    own_timer,
    pysx,
    resource,
    signal,
)
from pysx.async_runtime import MutationRuntime
from pysx.http import HTTPRuntime
from pysx.request import RequestContext
from pysx.server import Session

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysx import ErrorInfo, Signal


async def settle() -> None:
    for _ in range(12):
        await asyncio.sleep(0)


def test_serialized_async_latest_success_cancel_and_disposal() -> None:
    async def exercise() -> None:
        gates = [asyncio.Event(), asyncio.Event()]
        started = [asyncio.Event(), asyncio.Event()]
        loads = 0
        values: list[Resource[str]] = []

        async def load() -> str:
            nonlocal loads
            index = loads
            loads += 1
            started[index].set()

            try:
                await gates[index].wait()
            except asyncio.CancelledError:
                await gates[index].wait()

            return f"result-{index}"

        def app() -> Fragment:
            value = resource("load", load)
            values.append(value)

            return native.P(derived(lambda: value().status))

        session = Session(app)
        value = values[0]
        assert value().status == "pending"
        await started[0].wait()
        value.reload()
        await started[1].wait()
        gates[1].set()
        await settle()
        assert value().status == "success"
        assert value().value == "result-1"
        gates[0].set()
        await settle()
        assert value().value == "result-1"
        value.cancel()
        await settle()
        assert value().status == "cancelled"
        session.dispose()
        with pytest.raises(RuntimeError, match="disposed"):
            value.reload()
        await settle()
        assert not session.runtime.tasks

    asyncio.run(exercise())


def test_serialized_async_queue_owner_batch_and_capacity() -> None:
    async def exercise() -> None:
        runtime = MutationRuntime(capacity=2, tasks=2)
        value = signal(0)
        observed: list[int] = []
        observer = effect(lambda: observed.append(value()))
        gate = asyncio.Event()
        entered = asyncio.Event()

        async def hold() -> None:
            async with runtime.turn():
                entered.set()
                await gate.wait()

        owner = runtime.spawn(hold)
        await entered.wait()

        def mutate() -> int:
            value.set(1)
            value.set(2)

            return value()

        first = runtime.schedule(mutate)
        second = runtime.schedule(lambda: observed.append(3))
        with pytest.raises(asyncio.QueueFull):
            runtime.schedule(lambda: None)
        await asyncio.sleep(0)
        assert not first.done()
        runtime.spawn(hold)
        with pytest.raises(RuntimeError, match="capacity"):
            runtime.spawn(hold)
        gate.set()
        await owner
        assert await first == 2
        await second
        assert observed == [0, 2, 3]
        observer.dispose()
        runtime.close()
        await settle()

    asyncio.run(exercise())


def test_serialized_async_removed_scope_rejects_late_completion() -> None:
    async def exercise() -> None:
        visible = signal(True)
        started, gate = asyncio.Event(), asyncio.Event()
        values: list[Resource[str]] = []

        async def load() -> str:
            started.set()

            try:
                await gate.wait()
            except asyncio.CancelledError:
                await gate.wait()

            return "late"

        def child() -> Fragment:
            value = resource("child", load)
            values.append(value)

            return native.P(derived(lambda: value().value or "pending"))

        session = Session(lambda: pysx(t"\nif {visible}:\n  Child:", namespace={"Child": child}))
        await started.wait()
        visible.set(False)
        assert not values[0].owner.alive
        gate.set()
        await settle()
        assert values[0]().value is None
        assert not session.runtime.tasks
        session.dispose()

    asyncio.run(exercise())


def test_serialized_async_event_failure_barrier_and_foreign_write_guard() -> None:
    async def exercise() -> None:
        started, gate = asyncio.Event(), asyncio.Event()
        values: list[Signal[int]] = []

        def app() -> Fragment:
            value = signal(0)
            values.append(value)

            async def fail(_: object) -> None:
                value.set(1)
                started.set()
                await gate.wait()

                raise ValueError("private failure")

            return native.Div(native.Button("Fail", on_click=fail), native.P(value))

        session = Session(app)
        dispatch = asyncio.create_task(
            session.dispatch_result_async(next(iter(session.rendered.handlers)), None)
        )
        await started.wait()
        with pytest.raises(RuntimeError, match="serialized"):
            values[0].set(99)
        update = session.runtime.schedule(lambda: values[0].set(2))
        await asyncio.sleep(0)
        assert not update.done()
        gate.set()
        result = await dispatch
        assert result["status"] == "error"
        assert [op["v"] for op in result["ops"] if op["op"] == "text"] == ["1"]
        await update
        assert values[0]() == 2
        assert [op["v"] for op in session.pending if op["op"] == "text"] == ["2"]
        session.dispose()
        await settle()
        assert session.runtime.worker is not None
        assert session.runtime.worker.done()

    asyncio.run(exercise())


def test_serialized_async_rerendered_resource_keys_all_cancel_on_removal() -> None:
    async def exercise() -> None:
        rows = signal(["first"])
        started = {name: asyncio.Event() for name in ("first", "second")}
        cancelled: list[str] = []
        values: list[Resource[str]] = []

        def row(name: str) -> Fragment:
            async def load() -> str:
                started[name].set()

                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.append(name)

                return name

            value = resource(name, load)
            values.append(value)

            return native.P(name)

        session = Session(lambda: pysx(t"\nul: {each(rows, row, key=lambda _value: 'stable')}"))
        await started["first"].wait()
        rows.set(["second"])
        await started["second"].wait()
        assert values[0].owner is values[1].owner
        rows.set([])
        await settle()
        assert sorted(cancelled) == ["first", "second"]
        assert not session.runtime.tasks
        session.dispose()

    asyncio.run(exercise())


def test_serialized_async_no_inherited_effect_dependencies_and_timer_turn() -> None:
    async def exercise() -> None:
        unrelated = signal(1)
        trigger = signal(0)
        loaded: list[Resource[int]] = []
        states: list[Signal[int]] = []

        async def load() -> int:
            await asyncio.sleep(0)

            return unrelated()

        def app() -> Fragment:
            count = signal(0)
            states.append(count)
            value = resource("tracked", load)
            loaded.append(value)

            def reload() -> None:
                trigger()
                value.reload()

            own_effect("reload", reload)
            own_timer("change", 0.001, lambda: count.set(2))

            return native.Div(native.P(count), native.P(derived(lambda: value().status)))

        session = Session(app)
        await asyncio.sleep(0.02)
        assert loaded[0]().status == "success"
        assert not unrelated.observers
        assert states[0]() == 2
        assert any(op["op"] == "text" and op["v"] == "2" for op in session.pending)
        session.dispose()
        with pytest.raises(RuntimeError, match="disposed"):
            states[0].set(9)
        await settle()

    asyncio.run(exercise())


def test_serialized_async_failure_boundary_and_unclaimed_expiry() -> None:
    async def exercise() -> None:
        loaded: list[Resource[str]] = []

        async def bad() -> str:
            await asyncio.sleep(0)

            raise ValueError("PRIVATE_RESOURCE_DETAIL")

        def content() -> Fragment:
            value = resource("bad", bad)
            loaded.append(value)

            return native.P("Loading")

        def fallback(info: ErrorInfo, _retry: Callable[[], None]) -> Fragment:
            return native.P(info.message)

        boundary = ErrorBoundary(content, fallback)
        session = Session(lambda: pysx(t"\nBoundary:", namespace={"Boundary": boundary}))
        await settle()
        assert loaded[0]().status == "error"
        assert boundary.error is not None
        assert "PRIVATE" not in str(session.pending)
        session.dispose()
        await settle()
        assert not session.runtime.tasks

        async def never() -> str:
            await asyncio.Event().wait()

            return "late"

        values: list[Resource[str]] = []

        def pending() -> Fragment:
            value = resource("never", never)
            values.append(value)

            return native.P(derived(lambda: value().status))

        runtime = HTTPRuntime(pending, ttl=0.01)
        runtime.render(RequestContext(), "browser")
        await asyncio.sleep(0.03)
        assert runtime.pending == {}
        assert values[0].runtime.closed
        await settle()
        assert not values[0].runtime.tasks

    asyncio.run(exercise())


def test_serialized_async_owned_task_leaves_the_session_responsive() -> None:
    async def exercise() -> None:
        from pysx import own_task, push

        released = asyncio.Event()
        ticks = signal(0)

        async def worker() -> None:
            await released.wait()
            await push(lambda: ticks.set(ticks() + 100))

        def bump(_event: object) -> None:
            ticks.set(ticks() + 1)

        def app() -> Fragment:
            own_task("worker", worker)

            return native.Button("bump", on_click=bump)

        session = Session(app)

        try:
            (handler,) = session.rendered.handlers
            await settle()
            result = await asyncio.wait_for(session.dispatch_result_async(handler, None), 2)
            assert result["status"] == "success"
            assert ticks() == 1
            released.set()
            await settle()
            assert ticks() == 101
        finally:
            session.dispose()

    asyncio.run(exercise())


def test_serialized_async_task_admission_exceeds_the_mutation_queue() -> None:
    async def exercise() -> None:
        async def load() -> int:
            await asyncio.sleep(0)

            return 1

        def app() -> Fragment:
            for index in range(70):
                resource(f"item{index}", load)

            return native.P("many")

        session = Session(app)

        try:
            assert len(session.runtime.tasks) == 70
            await settle()
        finally:
            session.dispose()

        with pytest.raises(RuntimeError, match="capacity"):
            Session(app, task_capacity=8).dispose()

    asyncio.run(exercise())


@pytest.mark.parametrize("development", [False, True])
def test_serialized_async_resource_failure_logs_source_in_development(
    development: bool, caplog: pytest.LogCaptureFixture
) -> None:
    async def exercise() -> None:
        async def load() -> int:
            raise ValueError("private traceback secret")

        def app() -> Fragment:
            resource("broken", load)

            return native.P("resource")

        session = Session(app, development=development)

        try:
            await settle()
        finally:
            session.dispose()

    asyncio.run(exercise())
    assert bool(caplog.records[-1].exc_info) is development
    assert "private" not in caplog.records[-1].getMessage()
