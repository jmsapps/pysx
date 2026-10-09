"""Granular keyed rows, captured values and independent render ownership."""

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from pysx import Dom, Fragment, Signal, each, local_state, on_cleanup, own_effect, pysx, signal
from pysx.dom import DomError, DomRef
from pysx.render import ListWatcher
from pysx.server import Session

if TYPE_CHECKING:
    from pysx.wire import Op


@dataclass(frozen=True)
class Item:
    key: str
    label: str
    done: bool = False


def test_granular_keyed_field_edits_skip_unrelated_builders() -> None:
    rows = signal([Item("a", "old"), Item("b", "other")])
    built: list[str] = []
    captured: list[str] = []

    def row(item: Item) -> Fragment:
        built.append(item.key)

        def click(_event: object) -> None:
            captured.append(item.label)

        return pysx(t"""
            li(class={"done" if item.done else "active"}):
              button(onClick={click}): {item.label}
              input(checked={item.done}, type="checkbox")
        """)

    session = Session(lambda: pysx(t"ul: {each(rows, row, key=lambda item: item.key)}"))

    try:
        rows.set([Item("a", "new", True), rows()[1]])
        assert built == ["a", "b", "a"]
        assert len(session.pending) == 3
        assert {op["op"] for op in session.pending} == {"attr", "text"}
        handler = next(hid for hid in session.rendered.handlers if ":a:" in hid)
        session.dispatch(handler, None)
        assert captured == ["new"]
        session.pending.clear()
        rows.set(list(reversed(rows())))
        assert built == ["a", "b", "a"]
        assert session.pending == [{"op": "list", "id": "0", "keys": ["b", "a"], "html": {}}]
    finally:
        session.dispose()


def test_granular_keyed_local_signals_have_independent_watchers() -> None:
    rows = signal(["a"])
    counts: list[Signal[int]] = []
    drafts: list[Signal[str]] = []

    def row(_key: str) -> Fragment:
        count = signal(0)
        draft = signal("")
        counts.append(count)
        drafts.append(draft)

        return pysx(t"""
            span: {count}
            input(bindValue={draft})
        """)

    session = Session(lambda: pysx(t"div: {each(rows, row, key=str)}"))

    try:
        counts[0].set(4)
        assert len(counts) == 1
        assert session.pending == [{"op": "text", "id": "0:a:g1:0", "v": "4"}]
        assert len(rows.observers) == 1
        session.pending.clear()
        drafts[0].set("typing")
        assert len(drafts) == 1
        assert session.pending == [
            {"op": "attr", "id": "0:a:g1:e2", "name": "value", "v": "typing"}
        ]
        rows.set([])
        assert counts[0].observers == {}
    finally:
        session.dispose()


def test_granular_keyed_ref_tokens_follow_element_lifetimes() -> None:
    rows = signal([Item("a", "old")])
    refs: list[DomRef] = []

    def row(item: Item) -> Fragment:
        ref = Dom().ref()
        refs.append(ref)

        if item.done:
            return pysx(t"textarea(ref={ref}): {item.label}")

        return pysx(t"input(ref={ref}, value={item.label})")

    session = Session(lambda: pysx(t"div: {each(rows, row, key=lambda item: item.key)}"))

    try:
        original = refs[0].handle()
        token = original.token
        rows.set([Item("a", "new")])
        assert refs[-1].token == token
        assert token in session.rendered.dom.mounts
        assert all(op["op"] == "attr" for op in session.pending)
        session.pending.clear()
        rows.set([Item("a", "remounted", True)])
        assert refs[-1].token != token
        assert token not in session.rendered.dom.mounts
        with pytest.raises(DomError, match="stale"):
            asyncio.run(original.focus())
        assert any(op["op"] == "children" for op in session.pending)
    finally:
        session.dispose()


def test_granular_keyed_rebinding_disposes_old_attribute_sources() -> None:
    old, new = signal("old"), signal("new")
    rows = signal(["old"])
    sources = {"old": old, "new": new}

    def row(item: str) -> Fragment:
        return pysx(t"input(value={sources[item]})")

    session = Session(lambda: pysx(t"div: {each(rows, row, key=lambda _item: 'a')}"))

    try:
        assert len(old.observers) == 1
        rows.set(["new"])
        assert old.observers == {}
        assert len(new.observers) == 1
        session.pending.clear()
        old.set("ignored")
        assert session.pending == []
        new.set("current")
        assert session.pending == [
            {"op": "attr", "id": "0:a:g1:e1", "name": "value", "v": "current"}
        ]
    finally:
        session.dispose()
    assert new.observers == {}


def test_granular_keyed_nested_ranges_and_removed_handler_generations() -> None:
    rows, children = signal(["a"]), signal(["one", "two"])
    built: list[str] = []
    cleaned: list[str] = []
    captured: list[str] = []

    def leaf(key: str) -> Fragment:
        on_cleanup(lambda: cleaned.append(key))

        def click(_event: object) -> None:
            captured.append(key)

        return pysx(t"button(onClick={click}): {key}")

    def row(key: str) -> Fragment:
        built.append(key)
        state = local_state("state", 0)

        return pysx(t"""
            section: {state} {each(children, leaf, key=str)}
            footer: {key}
        """)

    session = Session(lambda: pysx(t"div: {each(rows, row, key=str)}"))

    try:
        old_handler = next(iter(session.rendered.handlers))
        children.set(["two", "one", "three"])
        assert built == ["a"]
        assert any(op["op"] == "list" and len(op["html"]) == 1 for op in session.pending)
        rows.set([])
        assert children.observers == {}
        rows.set(["a"])
        assert old_handler not in session.rendered.handlers
        assert session.dispatch(old_handler, None) == []
        assert captured == []
    finally:
        session.dispose()
    assert len(cleaned) == 6
    assert session.rendered.lists == {}
    assert session.rendered.refs == {}


def test_granular_keyed_duplicate_failure_is_atomic() -> None:
    rows = signal(["a", "b"])
    session = Session(lambda: pysx(t"div: {each(rows, lambda key: pysx(t'span: {key}'), key=str)}"))

    try:
        watcher = session.rendered.watchers[0]
        assert isinstance(watcher, ListWatcher)
        before = dict(watcher.rows)
        with pytest.raises(ExceptionGroup, match="reactive transaction") as failure:
            rows.set(["a", "a"])
        assert "duplicate" in str(failure.value.exceptions[0])
        assert watcher.rows == before
        assert session.pending == []
        rows.set(["b", "a"])
        assert len(re.findall("pysx:row", session.rendered.body)) == 4
    finally:
        session.dispose()


def test_identity_cleanup_hundreds_of_nested_churn_cycles() -> None:
    rows = signal([Item("a", "first"), Item("b", "second")])
    children = signal(["one", "two"])
    pulse, css = signal(0), signal("color: red")
    cleaned: list[str] = []
    captured: list[str] = []

    def leaf(key: str) -> Fragment:
        return pysx(t"span: {key}")

    def row(item: Item) -> Fragment:
        ref = Dom().ref()
        own_effect("pulse", lambda: pulse())
        on_cleanup(lambda: cleaned.append(item.key))

        def click(_event: object) -> None:
            captured.append(item.label)

        return pysx(t"""
            section(ref={ref}, css={css}):
              button(onClick={click}): {item.label}
              div: {each(children, leaf, key=str)}
        """)

    session = Session(lambda: pysx(t"div: {each(rows, row, key=lambda item: item.key)}"))

    def counts() -> tuple[int, ...]:
        out = session.rendered

        return (
            len(out.handlers),
            len(out.refs),
            len(out.dom.mounts),
            len(out.scopes.owners),
            len(out.lists),
            len(pulse.observers),
            len(css.observers),
            len(children.observers),
        )

    baseline = counts()

    try:
        for cycle in range(250):
            rows.set([Item("b", "second"), Item("a", str(cycle))])
            children.set(["two", "one", "extra"])
            children.set(["one", "two"])
            assert counts() == baseline
            handler = next(hid for hid in session.rendered.handlers if ":a:" in hid)
            session.dispatch(handler, None)
            assert captured[-1] == str(cycle)
            rows.set([Item("b", "second")])
            assert handler not in session.rendered.handlers
            session.dispatch(handler, None)
            assert len(captured) == cycle + 1
            rows.set([Item("a", "restored"), Item("b", "second")])
            assert counts() == baseline
            session.pending.clear()
        rows.set([])
        assert counts() == (0, 0, 0, 1, 1, 0, 0, 0)
    finally:
        session.dispose()
    assert len(cleaned) == 252
    assert session.rendered.lists == {}
    assert rows.observers == {}


def test_identity_cleanup_failure_does_not_leave_other_rows_alive() -> None:
    rows, pulse = signal(["a", "b"]), signal(0)
    closed: list[str] = []

    def row(key: str) -> Fragment:
        own_effect("pulse", lambda: pulse())

        def close() -> None:
            closed.append(key)

            if key == "a":
                raise ValueError("cleanup failure")

        on_cleanup(close)

        return pysx(t"span: {key}")

    session = Session(lambda: pysx(t"div: {each(rows, row, key=str)}"))
    with pytest.raises(ExceptionGroup, match="session cleanup"):
        session.dispose()
    assert closed == ["a", "b"]
    assert pulse.observers == {}
    assert rows.observers == {}
    assert session.rendered.scopes.owners == {}
    assert session.rendered.lists == {}
    session.dispose()


def test_identity_cleanup_live_snapshots_rebind_text_and_attributes() -> None:
    rows = signal([False])
    label = signal("seed")

    def row(constant: bool) -> Fragment:
        value = "seed" if constant else label

        return pysx(t"""
            section:
              span(title={value}): {value}
        """)

    session = Session(lambda: pysx(t"div: {each(rows, row, key=lambda _item: 'a')}"))

    try:
        label.set("live")
        session.pending.clear()
        rows.set([True])
        assert {(op["op"], op.get("v")) for op in session.pending} == {
            ("text", "seed"),
            ("attr", "seed"),
        }
        assert label.observers == {}
    finally:
        session.dispose()


def test_identity_cleanup_retained_conditional_ref_and_stale_branch_handlers() -> None:
    rows = signal(["first"])
    flag = signal(True)
    refs: list[DomRef] = []
    calls: list[str] = []

    def row(label: str) -> Fragment:
        ref = Dom().ref()
        refs.append(ref)

        def yes(_value: object) -> None:
            calls.append(label)

        def no(_value: object) -> None:
            calls.append("no")

        return pysx(t"""
            if {flag}:
              input(ref={ref}, onClick={yes})
            else:
              button(onClick={no}): "no"
            span: {label}
        """)

    session = Session(lambda: pysx(t"div: {each(rows, row, key=lambda _item: 'a')}"))

    try:
        token = refs[-1].token
        old = next(iter(session.rendered.handlers))
        rows.set(["current"])
        assert refs[-1].token == token
        session.dispatch(old, None)
        assert calls == ["current"]
        flag.set(False)
        assert old not in session.rendered.handlers
        session.dispatch(old, None)
        assert calls == ["current"]
        flag.set(True)
        assert old not in session.rendered.handlers
        assert refs[-1].token != token
    finally:
        session.dispose()


def test_identity_cleanup_replaced_and_readded_targets_reject_old_events() -> None:
    rows = signal(["button"])
    calls: list[str] = []

    def row(tag: str) -> Fragment:
        def click(_value: object) -> None:
            calls.append(tag)

        if tag == "button":
            return pysx(t'button(onClick={click}): "press"')

        if tag == "input":
            return pysx(t"input(onClick={click})")

        return pysx(t'span: "absent"')

    session = Session(lambda: pysx(t"div: {each(rows, row, key=lambda _item: 'a')}"))

    try:
        first = next(iter(session.rendered.handlers))
        rows.set(["input"])
        assert first not in session.rendered.handlers
        second = next(iter(session.rendered.handlers))
        session.dispatch(first, None)
        assert calls == []
        rows.set(["none"])
        rows.set(["input"])
        assert second not in session.rendered.handlers
        session.dispatch(second, None)
        assert calls == []
        session.dispatch(next(iter(session.rendered.handlers)), None)
        assert calls == ["input"]
    finally:
        session.dispose()


def test_identity_cleanup_new_branch_mount_precedes_setup_corrections() -> None:
    visible = signal(False)
    rows = signal(["a"])

    def row(_key: str) -> Fragment:
        value = signal("seed")

        # Evaluation happens when the fragment is rendered, before this component
        # returns; a nested component changes an already-emitted sibling slot.
        def change() -> Fragment:
            value.set("ready")

            return pysx(t'span: "mounted"')

        return pysx(t"""
            span: {value}
            {change}()
        """)

    session = Session(
        lambda: pysx(t"""
        div:
          if {visible}:
            {each(rows, row, key=str)}
    """)
    )

    try:
        visible.set(True)
        assert session.pending[0]["op"] == "html"
        assert "ready" in str(session.pending)
    finally:
        session.dispose()


def test_identity_cleanup_cached_nested_builder_keeps_handlers_and_styles() -> None:
    rows, children = signal(["before"]), signal(["one"])
    calls: list[str] = []
    built: list[str] = []

    def leaf(key: str) -> Fragment:
        built.append(key)
        css = "color: red"

        def click(_value: object) -> None:
            calls.append(key)

        return pysx(t"button(css={css}, onClick={click}): {key}")

    def row(label: str) -> Fragment:
        return pysx(t"""
            section:
              span: {label}
              {each(children, leaf, key=str)}
        """)

    session = Session(lambda: pysx(t"div: {each(rows, row, key=lambda _item: 'a')}"))

    try:
        handler = next(iter(session.rendered.handlers))
        css = session.rendered.styles.snapshot()
        rows.set(["after"])
        assert built == ["one"]
        assert handler in session.rendered.handlers
        assert session.rendered.styles.snapshot() == css
        session.dispatch(handler, None)
        assert calls == ["one"]
    finally:
        session.dispose()


def test_identity_cleanup_live_multiple_selection_rebinding_uses_property_patch() -> None:
    rows = signal([False])
    first, second = signal(["a"]), signal(["a"])

    def row(rebound: bool) -> Fragment:
        selected = second if rebound else first

        return pysx(t"""
            select(multiple={True}, bindSelected={selected}):
              option(value="a"): "A"
              option(value="b"): "B"
        """)

    session = Session(lambda: pysx(t"div: {each(rows, row, key=lambda _item: 'a')}"))

    try:
        first.set(["b"])
        session.pending.clear()
        rows.set([True])
        assert session.pending == [
            {"op": "prop", "id": "0:a:g1:e1", "name": "selectedValues", "v": ["a"]}
        ]
        assert first.observers == {}
        second.set(["b"])
        assert session.pending[-1]["op"] == "prop"
    finally:
        session.dispose()


def test_identity_cleanup_nested_branch_mount_records_stay_bounded() -> None:
    rows, outer, inner = signal(["a"]), signal(True), signal(True)

    def clicked(_value: object) -> None:
        pass

    def row(_key: str) -> Fragment:
        return pysx(t"""
            if {outer}:
              if {inner}:
                button(onClick={clicked}): "Nested"
            else:
              span: "Closed"
        """)

    session = Session(lambda: pysx(t"div: {each(rows, row, key=str)}"))

    try:
        baseline = len(session.rendered.branches)

        for _cycle in range(250):
            outer.set(False)
            assert len(session.rendered.branches) == 1
            assert not session.rendered.handlers
            outer.set(True)
            assert len(session.rendered.branches) == baseline
            assert len(session.rendered.handlers) == 1
        rows.set([])
        assert session.rendered.branches == {}
        assert session.rendered.elements == {}
        assert session.rendered.retired == set()
    finally:
        session.dispose()


def test_identity_cleanup_ref_owner_change_replaces_same_tag_target() -> None:
    rows = signal([False])
    refs: list[DomRef] = []

    def row(imperative: bool) -> Fragment:
        ref = Dom().ref(imperative=imperative)
        refs.append(ref)

        return pysx(t"div(ref={ref})")

    session = Session(lambda: pysx(t"section: {each(rows, row, key=lambda _item: 'a')}"))

    try:
        original = refs[0].handle()
        rows.set([True])
        assert refs[-1].token != original.token
        assert len(session.pending) == 1
        assert session.pending[0]["op"] == "children"
        assert session.pending[0]["range"] is True
        assert original.token not in session.rendered.dom.mounts
    finally:
        session.dispose()


def addressable(body: str, op: Op) -> bool:
    if op["op"] == "css":
        return True

    if op["op"] == "children" and op["range"]:
        token = op["id"].encode().hex()

        return f"pysx:{op['kind']}:{token}:start" in body

    return (
        f'data-pysx-el="{op["id"]}"' in body or f'pysx-slot id="{op["id"]}"' in body
    )


def test_granular_keyed_static_branch_flip_addresses_the_live_markup() -> None:
    rows = signal([Item("a", "off")])
    captured: list[str] = []

    def row(item: Item) -> Fragment:
        def click(_event: object) -> None:
            captured.append(item.label)

        return pysx(t"""
            li:
              if {item.done}:
                button(class="done", onClick={click}): {item.label}
              else:
                button(class="todo", onClick={click}): {item.label}
        """)

    session = Session(lambda: pysx(t"ul: {each(rows, row, key=lambda item: item.key)}"))

    try:
        before = session.rendered.body
        session.pending.clear()
        rows.set([Item("a", "on", True)])
        ops = list(session.pending)
        assert ops
        assert all(addressable(before, op) for op in ops)
        handler = next(hid for hid in session.rendered.handlers if ":a:" in hid)
        session.dispatch(handler, None)
        assert captured == ["on"]
    finally:
        session.dispose()


def test_granular_keyed_frozen_nested_list_patches_the_stale_range() -> None:
    @dataclass(frozen=True)
    class Group:
        key: str
        tags: tuple[str, ...]

    rows = signal([Group("a", ("x",))])

    def row(group: Group) -> Fragment:
        tags = each(group.tags, lambda tag: pysx(t"i: {tag}"), key=str)

        return pysx(t"li: {tags}")

    session = Session(lambda: pysx(t"ul: {each(rows, row, key=lambda group: group.key)}"))

    try:
        before = session.rendered.body
        session.pending.clear()
        rows.set([Group("a", ("y", "z"))])
        ops = list(session.pending)
        assert ops
        assert all(addressable(before, op) for op in ops)
        retained = session.rendered.lists["0"].markup["a"]
        assert ">y<" in retained
        assert ">x<" not in retained
    finally:
        session.dispose()


def test_granular_keyed_script_bodies_keep_their_raw_text() -> None:
    rows = signal(["a"])
    code = "if (a < b && c) { run(); }"

    def row(_key: str) -> Fragment:
        return pysx(t"""
            li:
              script: {code}
        """)

    session = Session(lambda: pysx(t"ul: {each(rows, row, key=str)}"))

    try:
        retained = session.rendered.lists["0"].markup["a"]
        assert "&amp;lt;" not in retained
        assert "&lt;pysx-slot" not in retained
        assert retained in session.rendered.body
    finally:
        session.dispose()
