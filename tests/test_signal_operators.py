"""Public operator behavior, identity dependencies and selective rendering."""

from enum import Enum
from typing import TYPE_CHECKING, Never, cast

import pytest

from pysx import (
    Signal,
    all_of,
    any_of,
    batch,
    concat,
    contains,
    derived,
    eq,
    ge,
    gt,
    inclusive_enum_range,
    inclusive_range,
    le,
    length,
    lt,
    ne,
    not_,
    signal,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize("name", ["eq", "ne", "lt", "le", "gt", "ge"])
@pytest.mark.parametrize("form", ["both", "left", "right"])
def test_operators_reactive_comparison_matrix(name: str, form: str) -> None:
    operations = {"eq": eq, "ne": ne, "lt": lt, "le": le, "gt": gt, "ge": ge}
    operation = cast("Callable[[object, object], Signal[bool]]", operations[name])
    left = signal(1)
    right = signal(2)
    result = operation(left if form != "right" else 1, right if form != "left" else 2)
    seen: list[bool] = []
    stop = result.subscribe(seen.append)
    assert seen == [
        {"eq": False, "ne": True, "lt": True, "le": True, "gt": False, "ge": False}[name]
    ]

    with batch():
        left.set(3)
        right.set(0)

    assert (
        seen[-1]
        == {"eq": False, "ne": True, "lt": False, "le": False, "gt": True, "ge": True}[name]
    )
    stop()
    assert not left.observers
    assert not right.observers


def test_operators_reactive_reflected_order_identity_truthiness() -> None:
    left = signal(1)
    other = signal(1)
    two = 2
    outputs = [
        left < two,
        left <= two,
        left > two,
        left >= two,
        two < left,
        two <= left,
        two > left,
        two >= left,
    ]
    assert [value() for value in outputs] == [True, True, False, False, False, False, True, True]
    left.set(3)
    assert [value() for value in outputs] == [False, False, True, True, True, True, False, False]
    assert left != other
    assert left == left

    with pytest.raises(TypeError):
        hash(left)

    with pytest.raises(TypeError, match="all_of"):
        bool(left)

    with pytest.raises(TypeError, match="all_of"):
        _ = 0 < left < 4

    invalid = cast("Callable[[object, object], Signal[bool]]", lt)(left, "bad")

    with pytest.raises(TypeError, match="Ordering"):
        invalid()


def test_operators_reactive_boolean_nary_tracks_all_and_suppresses() -> None:
    left, right = signal(False), signal(True)
    output = all_of(left, right)
    seen: list[bool] = []
    stop = output.subscribe(seen.append)
    assert len(left.observers) == len(right.observers) == 1
    right.set(False)
    assert seen == [False]
    left.set(True)
    right.set(True)
    assert seen == [False, True]
    assert all_of(False, right)() is False
    assert any_of(True, left)() is True
    assert not_(left)() is False
    assert not_(right)() is False
    assert any_of(left, right, False)() is True
    assert all_of()() is True
    assert any_of()() is False
    stop()
    assert not left.observers
    assert not right.observers


def test_operators_reactive_collections_ranges_and_empty() -> None:
    rows, item = signal([1, 2]), signal(2)
    present, size = contains(rows, item), length(rows)
    seen: list[bool] = []
    stop = present.subscribe(seen.append)
    rows.set([])
    assert seen == [True, False]
    assert size() == 0
    rows.set([3])
    item.set(3)
    assert seen == [True, False, True]
    stop()
    text = signal("abc")
    needle = signal("b")
    assert contains(text, needle)()
    assert concat(text, item)() == "abc3"
    assert contains(signal((1, 2)), 2)()
    assert contains(signal({1, 2}), 2)()
    assert contains(signal(frozenset({1, 2})), 2)()
    assert contains(signal(range(1, 3)), 2)()
    assert contains(inclusive_range(1, 3), item)()
    assert contains(signal(inclusive_range("a", "c")), "c")()

    class Ordinal(Enum):
        FIRST = 1
        LAST = 2

    assert contains(signal(inclusive_enum_range(Ordinal.FIRST, Ordinal.LAST)), Ordinal.LAST)()

    with pytest.raises(TypeError, match="contains"):
        rows.__contains__(cast("Never", 1))


def test_template_ergonomics_direct_holes_are_live_snapshots_are_plain() -> None:
    count = signal(1)
    live = count * 2
    snapshot = count()
    count.set(3)
    assert live() == 6
    assert snapshot == 1


def test_operators_reactive_equal_sources_dynamic_dependency_and_nested() -> None:
    left, right = signal(1), signal(1)
    total = derived(lambda: left() + right())
    seen: list[int] = []
    stop = total.subscribe(seen.append)
    left.set(2)
    right.set(2)
    assert seen == [2, 3, 4]
    stop()
    flag = signal(True)
    selected = derived(lambda: left() if flag() else right())
    stop = selected.subscribe(lambda _: None)
    flag.set(False)
    assert not left.observers
    stop()
    assert not right.observers
    assert not flag.observers

    with pytest.raises(TypeError, match="payloads"):
        signal(left)


def test_operators_reactive_booleans_are_not_ordered() -> None:
    # Both checkers reject ordering a Signal[bool] statically; this proves the
    # runtime refuses it too rather than silently ordering True as 1.
    flag = cast("Signal[int]", signal(True))

    with pytest.raises(TypeError, match="numeric pairs or string pairs"):
        (flag > 0).get()

    with pytest.raises(TypeError, match="numeric pairs or string pairs"):
        (flag < 1).get()

    with pytest.raises(TypeError, match="numeric pairs or string pairs"):
        (flag >= 0).get()

    with pytest.raises(TypeError, match="numeric pairs or string pairs"):
        (flag <= 1).get()
