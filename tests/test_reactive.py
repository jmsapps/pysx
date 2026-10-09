"""Reactive graph regression tests."""

import asyncio
from typing import TYPE_CHECKING, cast

import pytest

from pysx.reactive import Signal, batch, derived, effect, signal

if TYPE_CHECKING:
    from collections.abc import Callable


def test_identity_settled_cycle_and_lazy_comparator() -> None:
    calls: list[int] = []
    source = signal(1)

    def compute() -> int:
        calls.append(source())

        return source()

    value = derived(compute, equal=lambda left, right: abs(left) == abs(right))
    assert calls == []
    seen: list[int] = []
    stop = value.subscribe(seen.append)
    assert calls == [1]
    source.set(-1)
    assert seen == [1]
    stop()
    loop: Signal[int] = derived(lambda: loop())

    with pytest.raises(RuntimeError, match="cyclic"):
        loop()

    assert not loop.observers


def test_batching_transactions_computed_recovery() -> None:
    source = signal(0)

    def compute() -> int:
        value = source()

        if value == 1:
            raise ValueError("computed")

        return value * 2

    value = derived(compute)
    seen: list[int] = []
    stop = value.subscribe(seen.append)

    with pytest.raises(ExceptionGroup):
        source.set(1)

    source.set(2)
    assert seen == [0, 4]
    stop()


def test_batching_transactions_feedback_bound() -> None:
    source = signal(0)

    def feedback() -> None:
        value = source()

        if value:
            source.set(value + 1)

    observer = effect(feedback)

    with pytest.raises(ExceptionGroup, match="transaction"):
        source.set(1)

    observer.dispose()
    source.set(0)


def test_batching_transactions_async_boundary() -> None:
    source = signal(0)

    async def other() -> None:
        source.set(1)

    async def run() -> None:
        with batch():
            task = asyncio.create_task(other())

            with pytest.raises(RuntimeError, match="boundary"):
                await task

    asyncio.run(run())
    assert source() == 0


@pytest.mark.parametrize("fail", [False, True])
def test_batching_transactions_effect_nested_batch_releases_owner(fail: bool) -> None:
    source = signal(0)
    target = signal(0)

    def copy() -> None:
        value = source()

        if value:
            with batch():
                target.set(value)

                if fail:
                    raise ValueError("nested batch failed")

    observer = effect(copy)

    async def update() -> None:
        with batch():
            source.set(1)

    async def next_turn() -> None:
        with batch():
            target.set(2)
        assert target() == 2

    try:
        if fail:
            with pytest.raises(ExceptionGroup, match="reactive transaction"):
                asyncio.run(update())
        else:
            asyncio.run(update())
        assert target() == 1
        asyncio.run(next_turn())
    finally:
        observer.dispose()


def test_batching_transactions_nested_reads() -> None:
    a = signal(1)
    b = signal(2)
    total = derived(lambda: a() + b())
    seen: list[int] = []
    stop = total.subscribe(seen.append)

    with batch():
        a.set(3)

        with batch():
            b.set(4)
            assert total() == 7

        a.set(5)
        assert seen == [3]

    assert seen == [3, 9]
    stop()


def test_batching_transactions_failures_and_siblings() -> None:
    source = signal(0)
    seen: list[int] = []

    def bad(value: int) -> None:
        if value == 1:
            raise ValueError("observer failure")

    stop_bad = source.subscribe(bad)
    stop_good = source.subscribe(seen.append)

    with pytest.raises(ExceptionGroup, match="transaction"):
        source.set(1)

    assert seen == [0, 1]
    source.set(2)
    assert seen == [0, 1, 2]

    def fail_body() -> None:
        with batch():
            source.set(3)

            raise ValueError("body")

    with pytest.raises(ValueError, match="body"):
        fail_body()

    assert seen[-1] == 3
    stop_bad()
    stop_good()


def test_batching_transactions_cleanup_failure() -> None:
    source = signal(0)
    seen: list[int] = []

    def body() -> object:
        seen.append(source())

        def cleanup() -> None:
            raise ValueError("cleanup")

        return cleanup

    observer = effect(body)

    with pytest.raises(ExceptionGroup):
        source.set(1)

    observer.dispose()
    observer.dispose()
    assert not source.observers
    source.set(2)


def test_operators_reactive_subscription_owns_mutable_snapshots() -> None:
    source = signal([1])
    seen: list[list[int]] = []

    def mutate(value: list[int]) -> None:
        seen.append(value.copy())
        value.append(9)

    stop = source.subscribe(mutate)
    source.set([1])
    source.set([2])
    assert source() == [2]
    assert seen == [[1], [2]]
    stop()


def test_identity_settled_long_chain_fanout() -> None:
    source = signal(0)
    chain = source

    def increment(previous: Signal[int]) -> Signal[int]:
        return derived(lambda: previous() + 1)

    for _ in range(80):
        chain = increment(chain)

    seen: list[tuple[int, int]] = []
    observer = effect(lambda: seen.append((source(), chain())))
    source.set(1)
    assert seen == [(0, 80), (1, 81)]
    observer.dispose()
    assert not source.observers


def test_identity_settled_diamond_and_chain() -> None:
    source = signal(1)
    left = derived(lambda: source() * 2)
    right = derived(lambda: source() + 1)
    total = derived(lambda: left() + right())
    seen: list[int] = []
    stop = total.subscribe(seen.append)
    source.set(2)
    assert seen == [4, 7]
    stop()
    assert not source.observers
    source.set(3)
    assert total() == 10
    stop = total.subscribe(seen.append, fire=False)
    source.set(4)
    assert seen == [4, 7, 13]
    stop()
    stop()


def test_identity_settled_equal_and_dynamic() -> None:
    flag = signal(True)
    left = signal(1)
    right = signal(1)
    selected = derived(lambda: left() if flag() else right())
    parity = derived(lambda: selected() % 2)
    seen: list[int] = []
    stop = parity.subscribe(seen.append)
    left.set(3)
    assert seen == [1]
    flag.set(False)
    assert not left.observers
    right.set(2)
    assert seen == [1, 0]
    stop()


def test_identity_settled_unhashable_sources() -> None:
    class Unhashable(Signal[int]):
        __hash__ = None  # type: ignore[assignment]

        def __eq__(self, other: object) -> bool:
            raise AssertionError("graph compared dependencies")

    left = Unhashable(1)
    right = Unhashable(1)
    seen: list[int] = []
    observer = effect(lambda: seen.append(left() + right()))
    left.set(2)
    right.set(3)
    assert seen == [2, 3, 5]
    observer.dispose()


def test_identity_settled_callback_reads_are_untracked() -> None:
    source = signal(1)
    incidental = signal(2)
    seen: list[int] = []
    stop = source.subscribe(lambda value: seen.append(value + incidental()))
    incidental.set(9)
    assert seen == [3]
    source.set(2)
    assert seen == [3, 11]
    stop()


def test_basic_tracking() -> None:
    a = signal(1)
    seen: list[int] = []
    effect(lambda: seen.append(a()))
    assert seen == [1], seen
    a.set(2)
    assert seen == [1, 2], seen


def test_set_equal_is_noop() -> None:
    a = signal(1)
    runs: list[int] = []

    def body() -> None:
        a()
        runs.append(1)

    effect(body)
    assert len(runs) == 1
    a.set(1)
    assert len(runs) == 1, f"equal set re-ran the effect: {runs}"
    a.set(2)
    assert len(runs) == 2, runs


def test_dynamic_dependency_retracking() -> None:
    flag = signal(True)
    b = signal(10)
    runs: list[int | None] = []

    def body() -> None:
        runs.append(b() if flag() else None)

    effect(body)
    assert runs == [10], runs

    flag.set(False)  # body no longer reads b
    assert runs == [10, None], runs

    b.set(99)  # must NOT wake the effect
    assert runs == [10, None], f"stale subscription on b: {runs}"

    flag.set(True)  # re-subscribes to b
    assert runs == [10, None, 99], runs
    b.set(100)
    assert runs == [10, None, 99, 100], runs


def test_dispose() -> None:
    a = signal(1)
    runs: list[int] = []

    def body() -> None:
        a()
        runs.append(1)

    e = effect(body)
    e.dispose()
    a.set(2)
    assert len(runs) == 1, runs


def test_identity_settled_subscriber_write_during_notification() -> None:
    source = signal(10)
    doubled = derived(source.get)
    seen: list[int] = []

    def normalise(value: int) -> None:
        seen.append(value)

        if value == 10:
            source.set(20)

    stop = doubled.subscribe(normalise)

    try:
        assert seen == [10, 20]
        assert doubled() == 20
    finally:
        stop()


def test_batching_transactions_orphaned_computation_is_dequeued() -> None:
    rows = signal([1, 2, 3])
    last = derived(lambda: rows()[2])
    stop = last.subscribe(lambda _value: None)

    with batch():
        rows.set([1])
        stop()

    assert rows() == [1]


def test_batching_transactions_base_exception_clears_queues() -> None:
    source = signal(0)
    unrelated = signal(0)
    fired: list[str] = []

    def interrupt() -> None:
        if source():
            raise KeyboardInterrupt

    def record() -> None:
        source()
        fired.append("sibling")

    observer = effect(interrupt)
    sibling = effect(record)

    try:
        with pytest.raises(KeyboardInterrupt):
            source.set(1)

        unrelated.set(1)

        # A queue abandoned by the interrupt must not drain into a later,
        # unrelated transaction.
        assert fired == ["sibling"]
    finally:
        observer.dispose()
        sibling.dispose()


def test_batching_transactions_wide_fanout_is_not_feedback() -> None:
    source = signal(0)
    runs: list[int] = []

    def recorder(index: int) -> Callable[[int], None]:
        def record(_value: int) -> None:
            runs.append(index)

        return record

    stops = [source.subscribe(recorder(index), fire=False) for index in range(1100)]

    try:
        source.set(1)

        assert len(runs) == 1100
    finally:
        for stop in stops:
            stop()


def test_identity_settled_unchanged_identity_payload_suppresses() -> None:
    class Opaque:
        __hash__ = None  # type: ignore[assignment]

        def __eq__(self, other: object) -> bool:
            return self is other

    constant = Opaque()
    source = signal(0)
    output = derived(lambda: (source(), constant)[1])
    runs: list[int] = []
    stop = output.subscribe(lambda _value: runs.append(1), fire=False)

    try:
        for value in (1, 2, 3):
            source.set(value)

        # The payload never changes, so no downstream notification is owed.
        assert runs == []
    finally:
        stop()


def test_identity_settled_nested_signal_payloads_are_rejected() -> None:
    inner = signal(1)

    for payload in ({"a": inner}, [inner], (inner,), {"a": {"b": [inner]}}):
        with pytest.raises(TypeError, match="cannot be Signals"):
            signal(payload)

    holder = signal({"a": 1})

    # Both checkers already reject this statically; the guard defends the dynamic path.
    with pytest.raises(TypeError, match="cannot be Signals"):
        holder.set(cast("dict[str, int]", {"a": inner}))


def test_identity_settled_self_referencing_payload_terminates() -> None:
    cycle: list[object] = [1]
    cycle.append(cycle)

    assert signal(cycle)() is not None
