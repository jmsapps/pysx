"""Selective snapshots, positional projections and write-through ownership."""

from dataclasses import dataclass, replace

import pytest

from pysx import derived, dict_key, effect, list_index, project, structured


def test_structured_state_selective_nested_write() -> None:
    root = structured({"left": {"value": 1}, "right": {"value": 2}})
    left = dict_key(root, "left")
    value = dict_key(left, "value")
    calls: list[int] = []

    def compute() -> int:
        calls.append(value())

        return value() * 2

    result = derived(compute)
    seen: list[int] = []
    stop = result.subscribe(seen.append)
    dict_key(root, "right").set({"value": 8})
    assert calls == [1]
    assert seen == [2]
    value.set(3)
    assert root() == {"left": {"value": 3}, "right": {"value": 8}}
    assert calls == [1, 3]
    assert seen == [2, 6]
    stop()
    assert not root.observers


def test_structured_state_snapshot_ownership() -> None:
    original = {"rows": [1, 2]}
    root = structured(original)
    original["rows"].append(3)
    snapshot = root()
    snapshot["rows"].append(4)
    assert root() == {"rows": [1, 2]}
    rows = dict_key(root, "rows")
    rows().append(5)
    assert rows() == [1, 2]
    replacement = {"rows": [8]}
    root.set(replacement)
    replacement["rows"].append(9)
    assert rows() == [8]


def test_structured_state_subscription_snapshot() -> None:
    root = structured([1])
    seen: list[list[int]] = []

    def mutate_snapshot(value: list[int]) -> None:
        seen.append(value.copy())
        value.append(9)

    stop = root.subscribe(mutate_snapshot)
    root.set([1])
    root.set([2])
    assert seen == [[1], [2]]
    assert root() == [2]
    stop()


def test_structured_state_positions_deletion_recovery() -> None:
    rows = structured([1, 2, 3])
    item = list_index(rows, 1)
    seen: list[int] = []
    stop = item.subscribe(seen.append)
    rows.set([3, 1, 2])
    rows.update(lambda values: [9, *values])
    assert seen == [2, 1, 3]

    with pytest.raises(ExceptionGroup):
        rows.set([])

    with pytest.raises(IndexError):
        item.set(4)

    rows.set([6, 7])
    assert seen[-1] == 7
    item.set(8)
    assert rows() == [6, 8]
    stop()


def test_structured_state_objects_and_dynamic_paths() -> None:
    @dataclass(frozen=True)
    class Person:
        name: str
        age: int

    person = structured(Person("Ada", 30))
    name = project(person, lambda value: value.name, lambda value, name: replace(value, name=name))
    name.set("Grace")
    assert person() == Person("Grace", 30)
    root = structured({"a": 1, "b": 2})
    selected = structured("a")
    a = dict_key(root, "a")
    b = dict_key(root, "b")
    seen: list[int] = []
    observer = effect(lambda: seen.append(a() if selected() == "a" else b()))
    selected.set("b")
    a.set(4)
    assert seen == [1, 2]
    root.set({"a": 0, "b": 3})
    assert seen == [1, 2, 3]
    observer.dispose()


def test_structured_state_missing_key() -> None:
    root = structured({"a": 1})
    missing = dict_key(root, "b")

    with pytest.raises(KeyError):
        missing()

    with pytest.raises(KeyError):
        missing.set(2)

    assert root() == {"a": 1}
