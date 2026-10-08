"""Inline Python helpers preserve snapshot, keyed and lazy ownership contracts."""

from typing import TYPE_CHECKING, cast

import pytest

from pysx import Fragment, Signal, each, each_indexed, local_state, on_cleanup, pysx, signal, when
from pysx.compiler import analyze
from pysx.render import ListWatcher
from pysx.server import Session

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


def test_iterable_is_frozen_without_subscriptions() -> None:
    values = [1, 2]
    count = signal(3)
    rows = each(values, lambda value: pysx(t"p: {value} {count}"), key=str)
    values.append(4)
    session = Session(lambda: pysx(t"div: {rows}"))
    assert 'data-pysx-key="4"' not in session.rendered.body
    assert session.rendered.watchers == []
    assert count.observers == {}
    session.dispose()


def test_iterators_are_bounded_before_building() -> None:
    visited: list[int] = []

    def infinite() -> Iterator[int]:
        index = 0

        while True:
            visited.append(index)
            yield index
            index += 1

    with pytest.raises(ValueError, match="10000"):
        each(infinite(), lambda value: pysx(t"p: {value}"), key=str)
    assert len(visited) == 10001


def test_live_source_bounds_precede_builders() -> None:
    built: list[int] = []

    def row(value: int) -> Fragment:
        built.append(value)

        return pysx(t"p: {value}")

    with pytest.raises(ValueError, match="10000"):
        Session(lambda: pysx(t"div: {each(lambda: range(10001), row, key=str)}"))
    assert built == []


def test_indexed_snapshot_and_lazy_snapshot_branch() -> None:
    source = signal(1)
    rows = each_indexed(["a"], lambda index, value: pysx(t"p: {index} {value}"), key=str)
    branch = when(conditions=[(True, lambda: pysx(t"p: {source}"))])
    session = Session(lambda: pysx(t"div: {[rows, branch]}"))
    assert source.observers == {}
    assert session.rendered.watchers == []
    assert ">0</pysx-slot>" in session.rendered.body
    session.dispose()


@pytest.mark.parametrize("key", [True, 1.5, None, object()])
def test_invalid_keys_fail_before_building(key: object) -> None:
    built: list[int] = []

    def row(value: int) -> Fragment:
        built.append(value)

        return pysx(t"p: {value}")

    with pytest.raises(TypeError, match="keys"):
        Session(lambda: pysx(t"div: {each([1], row, key=lambda _: cast('str', key))}"))
    assert built == []


def test_duplicate_refresh_preserves_current_handlers_and_owners() -> None:
    rows = signal([1, 2])

    def row(value: int) -> Fragment:
        def click(_: object) -> int:

            return value

        return pysx(t"button(onClick={click}): {value}")

    session = Session(lambda: pysx(t"div: {each(rows, row, key=str)}"))
    handlers = dict(session.rendered.handlers)
    owners = dict(session.rendered.scopes.owners)

    with pytest.raises(ExceptionGroup, match="reactive transaction"):
        rows.set([1, 1])
    assert session.rendered.handlers == handlers
    assert session.rendered.scopes.owners == owners
    rows.set([2, 1])
    session.dispose()


def test_mixed_wire_keys_collide() -> None:
    keys: list[str | int] = [1, "1"]

    with pytest.raises(ValueError, match="duplicate list key"):
        Session(lambda: pysx(t"div: {each(keys, lambda _: pysx(t'p: x'), key=lambda x: x)}"))


def test_keyed_state_index_and_captured_handlers_refresh() -> None:
    rows = signal([("a", "old"), ("b", "second")])
    states: dict[str, Signal[int]] = {}
    captured: list[str] = []

    def row(index: int, item: tuple[str, str]) -> Fragment:
        state = local_state("count", 0)
        states[item[0]] = state

        def click(_: object) -> None:
            captured.append(item[1])

        return pysx(t"button(onClick={click}): {index} {item[1]} {state}")

    session = Session(lambda: pysx(t"div: {each_indexed(rows, row, key=lambda item: item[0])}"))
    original = states["a"]
    original.set(7)
    rows.set([("b", "second"), ("a", "new")])
    assert states["a"] is original
    watcher = session.rendered.watchers[0]
    assert isinstance(watcher, ListWatcher)
    assert ">1</pysx-slot>" in watcher.markup["a"]
    assert ">new</pysx-slot>" in watcher.markup["a"]
    assert ">7</pysx-slot>" in watcher.markup["a"]
    handler = next(value for key, value in session.rendered.handlers.items() if ":a:" in key)
    handler(None)
    assert captured == ["new"]
    session.dispose()


def test_nested_readable_children_are_live_through_parent() -> None:
    parents = signal(["parent"])
    children = signal(["first"])

    def row(parent: str) -> Fragment:

        return pysx(t"div: {each(children, lambda child: pysx(t'p: {parent} {child}'), key=str)}")

    session = Session(lambda: pysx(t"div: {each(parents, row, key=str)}"))
    children.set(["second"])
    assert any("second" in str(op) for op in session.pending)
    assert children.observers
    parents.set([])
    assert children.observers == {}
    session.dispose()
    assert parents.observers == {}


def test_when_first_match_laziness_default_state_and_cleanup() -> None:
    first = signal(True)
    second = signal(True)
    built: list[str] = []
    cleanups: list[str] = []
    states: dict[str, Signal[int]] = {}

    def branch(name: str) -> Fragment:
        built.append(name)
        states[name] = local_state("count", 0)
        on_cleanup(lambda: cleanups.append(name))

        return pysx(t"p: {name} {states[name]}")

    choice = when(
        conditions=[(first, lambda: branch("first")), (second, lambda: branch("second"))],
        default=lambda: branch("default"),
    )
    session = Session(lambda: pysx(t"div: {choice}"))
    assert set(built) == {"first"}
    assert second.observers == {}
    original = states["first"]
    original.set(4)
    assert states["first"] is original
    first.set(False)
    assert built[-1] == "second"
    assert cleanups == ["first"]
    with pytest.raises(RuntimeError, match="disposed"):
        original.set(5)
    second.set(False)
    assert built[-1] == "default"
    assert cleanups == ["first", "second"]
    first.set(True)
    assert states["first"] is not original
    assert second.observers == {}
    session.dispose()
    assert first.observers == {}
    assert sorted(cleanups) == ["default", "first", "first", "second"]


def test_when_no_match_and_invalid_condition() -> None:
    session = Session(lambda: pysx(t"div: {when(conditions=[(False, lambda: pysx(t'p: no'))])}"))
    assert "data-pysx-key" not in session.rendered.body
    session.dispose()

    with pytest.raises(TypeError, match="conditions"):
        Session(
            lambda: pysx(t"div: {when(conditions=[(cast('bool', 1), lambda: pysx(t'p: no'))])}")
        )


def test_delayed_builders_keep_local_component_bindings() -> None:
    source = '''from pysx import each, pysx, signal, styled, when
rows = signal(["first"])
flag = signal(True)
def app():
    Card = styled.article(t"color: red")
    return pysx(t"""
    div:
      {each(rows, lambda value: pysx(t'Card: {value}'), key=str)}
      {when(conditions=[(flag, lambda: pysx(t'Card: "selected"'))])}
    """)
'''
    compilation = analyze(source)
    assert compilation.complete
    namespace: dict[str, object] = {}
    exec(compilation.code(), namespace)
    session = Session(cast("Callable[[], Fragment]", namespace["app"]))
    rows = cast("Signal[list[str]]", namespace["rows"])
    flag = cast("Signal[bool]", namespace["flag"])
    rows.set(["later"])
    assert any("<article" in str(op) and "later" in str(op) for op in session.pending)
    flag.set(False)
    flag.set(True)
    assert any("selected" in str(op) for op in session.pending)
    session.dispose()
    assert rows.observers == {}
    assert flag.observers == {}
