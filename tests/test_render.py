"""Rendering, reactive attributes, branches, keyed lists, and bindings."""

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import TYPE_CHECKING, cast

from examples.counter import app
from pysx import (
    Fragment,
    Signal,
    batch,
    component,
    derived,
    dict_key,
    div,
    each,
    html,
    render,
    signal,
    structured,
    styled,
)
from pysx.server import Session

if TYPE_CHECKING:
    from collections.abc import Callable


def test_serialization_live_boolean_classes_and_properties() -> None:
    hidden = signal(False)
    value = signal("initial")
    classes = signal({"active": True, "inactive": False})

    def view() -> Fragment:
        return html(t"""
            input(checked={hidden} ariaChecked={hidden} value={value})
            div(className="base active" class={classes})
        """)

    session = Session(view)
    assert 'aria-checked="false"' in session.rendered.body
    assert " checked" not in session.rendered.body
    assert 'class="base active"' in session.rendered.body
    hidden.set(True)
    value.set("corrected")
    classes.set({"new": True})
    assert {"op": "attr", "id": "e1", "name": "checked", "v": ""} in session.pending
    assert {"op": "attr", "id": "e1", "name": "aria-checked", "v": "true"} in session.pending
    assert {"op": "attr", "id": "e1", "name": "value", "v": "corrected"} in session.pending
    assert {"op": "attr", "id": "e2", "name": "class", "v": "base active new"} in session.pending
    session.dispose()


def test_serialization_live_key_and_class_escaping() -> None:
    rows = signal(['key":<&'])

    def row(value: str) -> Fragment:
        return html(t"""\n        div(class={value}): {value}\n        """)

    def view() -> Fragment:
        return html(t"""\n        div: {each(rows, row, key=lambda value: value)}\n        """)

    markup = render(view).body
    assert 'data-pysx-key="key&quot;:&lt;&amp;"' in markup
    assert 'class="key&quot;:&lt;&amp;"' in markup
    assert 'id="0:key&quot;:&lt;&amp;:1"' in markup


def test_structured_state_selective_patches_and_event_batch() -> None:
    root = structured({"left": 1, "right": 2})
    left = dict_key(root, "left")

    def app_fn() -> Fragment:
        return html(t"""\n        p: {left}\n        """)

    session = Session(app_fn)
    dict_key(root, "right").set(3)
    assert session.pending == []

    with batch():
        left.set(4)
        left.set(5)

    assert session.pending == [{"op": "text", "id": "0", "v": "5"}]
    session.dispose()
    assert not root.observers


def test_template_ergonomics_operator_text_branch_isolation_and_disposal() -> None:
    state = signal({"scores": [1], "other": [9]})
    score = state["scores"][0]
    doubled = score * 2
    visible = score > 0

    def app_fn() -> Fragment:
        return html(t"""
            div: {doubled}
            if {visible}:
                p: "visible"
        """)

    session = Session(app_fn)
    other_session = Session(app)
    state["other"].set([8])
    assert session.pending == []
    score.set(2)
    assert session.pending == [{"op": "text", "id": "0", "v": "4"}]
    session.pending.clear()
    score.set(0)
    assert len(session.pending) == 2
    assert other_session.pending == []
    session.dispose()
    other_session.dispose()
    assert not state.observers


def test_template_ergonomics_branch_removal_releases_operator_sources() -> None:
    visible = signal(True)
    count = signal(1)
    doubled = count * 2

    def app_fn() -> Fragment:
        return html(t"""
            if {visible}:
                p: {doubled}
        """)

    session = Session(app_fn)
    assert count.observers
    visible.set(False)
    assert not count.observers
    session.pending.clear()
    count.set(2)
    assert session.pending == []
    visible.set(True)
    assert "4" in str(session.pending)
    session.dispose()
    assert not count.observers
    assert not visible.observers


def test_batching_transactions_event_patches() -> None:
    count = signal(0)

    def click(_value: object) -> None:
        count.set(1)
        count.set(2)

    def app_fn() -> Fragment:
        return html(t"""
        button(onClick={click}): "change"
        p: {count}
        """)

    session = Session(app_fn)
    handler = next(iter(session.rendered.handlers))
    ops = session.dispatch(handler, None)
    assert len(ops) == 1
    assert ops[0]["op"] == "text"
    assert ops[0]["v"] == "2"
    session.dispose()


class _Attrs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    @classmethod
    def of(cls, markup: str) -> list[tuple[str, dict[str, str | None]]]:
        p = cls()
        p.feed(markup)

        return p.tags


def test_button_carries_real_data_attribute() -> None:
    r = render(app)
    tags = _Attrs.of(r.body)
    buttons = [a for t, a in tags if t == "button"]
    assert len(buttons) == 1, tags
    attrs = buttons[0]
    assert attrs.get("data-pysx-click") == "h1", attrs
    assert "onclick" not in attrs, attrs
    assert attrs.get("type") == "button", attrs


def test_styled_class_applied_and_each_rule_defined_once() -> None:
    r = render(app)
    tags = _Attrs.of(r.body)
    used = {a["class"] for _, a in tags if "class" in a}
    assert len(used) >= 2, used

    for cls in used:
        assert cls is not None
        assert cls.startswith("pysx-"), cls
        assert r.css.count(f".{cls} {{") == 1, cls  # defined exactly once


def test_identical_css_collapses_to_one_class() -> None:
    from pysx import div, styled, stylesheet

    a = styled(div, t"""color: rebeccapurple;""")
    b = styled(div, t"""color: rebeccapurple;""")
    assert a.css_class == b.css_class, (a, b)
    assert stylesheet().count(f".{a.css_class} {{") == 1


def test_slot_prefilled() -> None:
    r = render(app)
    assert '<pysx-slot id="0">0</pysx-slot>' in r.body, r.body


def test_handler_registered_and_watcher_emits() -> None:
    r = render(app)
    assert [type(w).__name__ for w in r.watchers] == ["TextWatcher"]
    r.handlers["h1"](None)
    assert r.watchers[0].refresh() == [{"op": "text", "id": "0", "v": "1"}]
    assert r.watchers[0].refresh() == [], "unchanged value must emit nothing"


def test_sessions_do_not_share_state() -> None:
    a, b = render(app), render(app)
    a.handlers["h1"](None)
    a.handlers["h1"](None)
    assert a.watchers[0].refresh() == [{"op": "text", "id": "0", "v": "2"}]
    assert b.watchers[0].refresh() == [], "module-scope signal leaked across renders"


def test_hole_value_is_escaped() -> None:
    from pysx import component, div, html, styled

    Box = styled(div, t"""color: red;""")  # noqa: N806 - DSL component name
    payload = signal("<script>alert(1)</script>")

    ns = {"Box": Box}

    @component
    def view() -> Fragment:
        return html(t"""
            Box:
                {payload}
        """)

    view.__globals__.update(ns)
    r = render(view)
    assert "<script>" not in r.body, r.body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.body, r.body


Box = styled(div, t"""color: red;""")


def mk(fn: Callable[[], Fragment]) -> Callable[[], Fragment]:
    """Give a component a namespace the renderer can resolve tags from."""
    cast("dict[str, object]", fn.__globals__).setdefault("Box", Box)

    return component(fn)


def test_reactive_attribute_gets_element_id_and_emits_attr_op() -> None:
    cls = signal("a")

    @mk
    def view() -> Fragment:
        return html(t"""
            Box(class={cls}):
                "x"
        """)

    r = render(view)
    assert 'data-pysx-el="e1"' in r.body, r.body
    rendered = r.body.split('class="')[1].split('"')[0]
    assert rendered == f"{Box.css_class} a", rendered
    cls.set("b")
    # The op carries the merged class list, not just the hole's value.
    assert r.watchers[0].refresh() == [
        {"op": "attr", "id": "e1", "name": "class", "v": f"{Box.css_class} b"}
    ], r.watchers[0].refresh()


def test_class_patch_keeps_the_scoped_class() -> None:
    """A class patch replaces the whole attribute, so it must carry the styled
    hash too or the element loses all of its styling on the first click."""
    mode = signal("a")
    active = derived(lambda: "is-active" if mode() == "a" else "")

    @mk
    def view() -> Fragment:
        return html(t"""
            Box(class={active}):
                "x"
        """)

    r = render(view)
    rendered = r.body.split('class="')[1].split('"')[0]
    assert rendered.startswith("pysx-"), rendered
    assert rendered.endswith(" is-active"), rendered
    mode.set("b")
    ops = [o for w in r.watchers for o in w.refresh()]
    assert len(ops) == 1, ops
    assert ops[0]["op"] == "attr"
    assert ops[0]["v"] is not None
    assert ops[0]["v"].startswith("pysx-"), f"scoped class dropped: {ops[0]}"
    assert "is-active" not in ops[0]["v"], ops[0]
    mode.set("a")
    ops = [o for w in r.watchers for o in w.refresh()]
    assert ops[0]["op"] == "attr"
    assert ops[0]["v"] == rendered, (ops[0], rendered)


def test_boolean_attribute_present_then_removed() -> None:
    on = signal(True)

    @mk
    def view() -> Fragment:
        return html(t"""
            Box(hidden={on}):
                "x"
        """)

    r = render(view)
    assert " hidden" in r.body, r.body
    assert 'hidden="' not in r.body, r.body
    on.set(False)
    assert r.watchers[0].refresh() == [{"op": "attr", "id": "e1", "name": "hidden", "v": None}], (
        "falsey boolean attribute must send null, not the string 'false'"
    )


def test_conditional_swaps_branch_html() -> None:
    flag = signal(True)

    @mk
    def view() -> Fragment:
        return html(t"""
            Box:
                if {flag}:
                    span: "yes"
                else:
                    span: "no"
        """)

    r = render(view)
    assert "<span>yes</span>" in r.body, r.body
    flag.set(False)
    ops = r.watchers[0].refresh()
    assert ops == [{"op": "html", "id": "0", "v": "<span>no</span>"}], ops


@dataclass
class Row:
    id: int
    text: str


def _list_view(items: Signal[list[Row]]) -> Callable[[], Fragment]:
    @component
    def item(row: Row) -> Fragment:
        return html(t"""
            li: {row.text}
        """)

    @mk
    def view() -> Fragment:
        return html(t"""
            Box:
                {each(items, item, key=(lambda r: r.id))}
        """)

    return view


def test_keyed_list_renders_with_keys() -> None:
    items = signal([Row(1, "a"), Row(2, "b")])
    r = render(_list_view(items))
    assert '<pysx-list id="0">' in r.body, r.body
    assert 'data-pysx-key="1"' in r.body, r.body
    assert 'data-pysx-key="2"' in r.body, r.body


def test_unchanged_items_send_no_html() -> None:
    items = signal([Row(1, "a"), Row(2, "b")])
    r = render(_list_view(items))
    items.set([Row(2, "b"), Row(1, "a")])  # reorder only
    ops = r.watchers[0].refresh()
    assert len(ops) == 1, ops
    assert ops[0]["op"] == "list", ops
    assert ops[0]["keys"] == ["2", "1"], ops
    assert ops[0]["html"] == {}, "a pure reorder must send zero html"


def test_only_the_changed_item_sends_html() -> None:
    items = signal([Row(1, "a"), Row(2, "b")])
    r = render(_list_view(items))
    items.set([Row(1, "a"), Row(2, "CHANGED")])
    ops = r.watchers[0].refresh()
    assert ops[0]["op"] == "list"
    assert list(ops[0]["html"]) == ["2"], ops[0]["html"]
    assert "CHANGED" in ops[0]["html"]["2"]


def test_no_change_emits_nothing() -> None:
    items = signal([Row(1, "a")])
    r = render(_list_view(items))
    items.set([Row(1, "a")])
    assert r.watchers[0].refresh() == []


def test_bind_value_renders_value_and_input_hook() -> None:
    draft = signal("hi")

    @mk
    def view() -> Fragment:
        return html(t"""
            Box:
                input(bindValue={draft})
        """)

    r = render(view)
    assert 'value="hi"' in r.body, r.body
    assert "data-pysx-input=" in r.body, r.body
    hid = r.body.split('data-pysx-input="')[1].split('"')[0]
    r.handlers[hid]("typed")
    assert draft() == "typed"
