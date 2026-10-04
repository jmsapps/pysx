"""Behavioral operator proofs exported to the authoring aggregate runner."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, cast

import pytest

from .operators import (
    Signal,
    all_of,
    any_of,
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
)

if TYPE_CHECKING:
    from collections.abc import Callable, Container, Sized
    from pathlib import Path


def comparisons(_scratch: Path) -> None:
    left, right = Signal(1), Signal(2)
    two, three, upper = 2, 3, "b"
    outputs = [
        eq(left, right),
        ne(left, right),
        lt(left, right),
        le(left, right),
        gt(left, right),
        ge(left, right),
        eq(2, left),
        ne(2, left),
        lt(2, left),
        le(2, left),
        gt(2, left),
        ge(2, left),
        left < 2,
        left <= 2,
        left > 2,
        left >= 2,
        two < left,
        two <= left,
        two > left,
        two >= left,
    ]
    assert [item.get() for item in outputs] == [
        False,
        True,
        True,
        True,
        False,
        False,
        False,
        True,
        False,
        False,
        True,
        True,
        True,
        True,
        False,
        False,
        False,
        False,
        True,
        True,
    ]
    left.set(3)
    assert [item.get() for item in outputs] == [
        False,
        True,
        False,
        False,
        True,
        True,
        False,
        True,
        True,
        True,
        False,
        False,
        False,
        False,
        True,
        True,
        True,
        True,
        False,
        False,
    ]
    right.set(3)
    assert [item.get() for item in outputs[:6]] == [True, False, False, True, False, True]
    assert (Signal("a") < "b").get()
    assert (upper > Signal("a")).get()
    assert (Signal(2) < 2.5).get()
    assert (two < Signal(2.5)).get()
    # Equality dunders deliberately answer object identity, never payload equality.
    assert left == left
    assert left != right
    assert (three == left) is False
    for item in outputs:
        item.dispose()
    assert not left.observers
    assert not right.observers


def identity_and_dependencies(_scratch: Path) -> None:
    left, right = Signal(1), Signal(1)
    visits: list[int] = []

    def calculate() -> int:
        result = left.get() + right.get()
        visits.append(result)
        return result

    output = derived(calculate)
    assert visits == [2]
    assert output.keeper is not None
    assert set(output.keeper.dependencies) == {id(left), id(right)}
    with pytest.raises(TypeError):
        hash(left)
    left.set(2)
    assert output.get() == 3
    right.set(2)
    assert output.get() == 4
    assert visits[-2:] == [3, 4]
    output.dispose()
    assert not left.observers
    assert not right.observers
    count = len(visits)
    left.set(3)
    assert len(visits) == count

    switch, primary, secondary = Signal(True), Signal(10), Signal(20)
    selected = derived(lambda: primary.get() if switch.get() else secondary.get())
    switch.set(False)
    assert selected.get() == 20
    assert not primary.observers
    assert secondary.observers
    selected.dispose()
    assert not switch.observers
    assert not secondary.observers


def boolean_and_protocols(_scratch: Path) -> None:
    left, right = Signal(False), Signal(True)
    outputs = [
        all_of(left, right),
        any_of(left, right),
        not_(left),
        left & right,
        False & right,
        left | right,
        True | left,
        ~left,
    ]
    assert [item.get() for item in outputs] == [False, True, True, False, False, True, True, True]
    for item in outputs:
        assert item.keeper is not None
    # All operands are tracked even when the first value settles the boolean result.
    assert outputs[0].keeper is not None
    assert set(outputs[0].keeper.dependencies) == {id(left), id(right)}
    left.set(True)
    right.set(False)
    assert [item.get() for item in outputs] == [False, True, False, False, False, True, True, False]
    with pytest.raises(TypeError, match="all_of"):
        bool(left)
    with pytest.raises(TypeError, match="all_of"):
        _ = 0 < Signal(1) < 2
    assert all_of().get()
    assert not any_of().get()
    for item in outputs:
        item.dispose()
    assert not left.observers
    assert not right.observers


def collections(_scratch: Path) -> None:
    rows, item = Signal([1, 2]), Signal(2)
    result, size = contains(rows, item), length(rows)
    text, needle = Signal("abc"), Signal("b")
    has_text = contains(text, needle)
    combined = concat(text, item)
    assert result.get()
    assert size.get() == 2
    assert has_text.get()
    assert combined.get() == "abc2"
    rows.set([1])
    item.set(1)
    assert result.get()
    assert size.get() == 1
    assert combined.get() == "abc1"
    needle.set("z")
    assert not has_text.get()
    text.set("z")
    assert has_text.get()
    assert combined.get() == "z1"
    assert contains(Signal((1, 2)), 2).get()
    assert contains(Signal({1, 2}), 2).get()
    assert contains(Signal(frozenset({1, 2})), 2).get()
    assert contains((1, 2), item).get()
    assert contains({1, 2}, item).get()
    assert contains(frozenset({1, 2}), item).get()
    assert contains("z", needle).get()
    assert contains(Signal(inclusive_range("a", "c")), "c").get()
    assert not contains(Signal(inclusive_range("a", "c")), "d").get()
    assert inclusive_range("c", "a") == ()
    with pytest.raises(TypeError, match="single-character"):
        inclusive_range("ab", "cd")

    class Ordinal(Enum):
        FIRST = "first"
        MIDDLE = "middle"
        LAST = "last"

    ordinals = Signal(inclusive_enum_range(Ordinal.FIRST, Ordinal.LAST))
    assert contains(ordinals, Ordinal.MIDDLE).get()
    assert inclusive_enum_range(Ordinal.LAST, Ordinal.FIRST) == ()
    bounds = Signal(inclusive_range(1, 3))
    assert contains(bounds, 3).get()
    assert not contains(bounds, 4).get()
    assert contains(inclusive_range(1, 3), item).get()
    # Primitive protocols operate on an explicit payload snapshot.
    assert len(rows.get()) == 1
    assert (1 in rows.get()) is True
    with pytest.raises(TypeError):
        len(cast("Sized", rows))
    with pytest.raises(TypeError):
        _ = 1 in cast("Container[int]", rows)
    for output in (result, size, has_text, combined):
        output.dispose()
    assert not rows.observers
    assert not text.observers
    with pytest.raises(TypeError, match="nested Signals"):
        Signal(Signal(1))


CASES: dict[str, Callable[[Path], None]] = {
    "comparison_matrix": comparisons,
    "identity_graph": identity_and_dependencies,
    "boolean_protocols": boolean_and_protocols,
    "collection_helpers": collections,
}
