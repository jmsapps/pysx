"""Renderer coverage for the hole kinds todos.nim needs."""

import sys
from dataclasses import dataclass

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

from pysx import component, derived, div, each, html, signal, styled  # noqa: E402
from pysx.render import render  # noqa: E402

Box = styled(div, t"""color: red;""")


def mk(fn):
    """Give a component a namespace the renderer can resolve tags from."""
    fn.__globals__.setdefault("Box", Box)
    return component(fn)


def test_reactive_attribute_gets_element_id_and_emits_attr_op():
    cls = signal("a")

    @mk
    def view():
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


def test_class_patch_keeps_the_scoped_class():
    """A class patch replaces the whole attribute, so it must carry the styled
    hash too or the element loses all of its styling on the first click."""
    mode = signal("a")
    active = derived(lambda: "is-active" if mode() == "a" else "")

    @mk
    def view():
        return html(t"""
            Box(class={active}):
                "x"
        """)

    r = render(view)
    rendered = r.body.split('class="')[1].split('"')[0]
    assert rendered.startswith("pysx-") and rendered.endswith(" is-active"), rendered
    mode.set("b")
    ops = [o for w in r.watchers for o in w.refresh()]
    assert len(ops) == 1, ops
    assert ops[0]["v"].startswith("pysx-"), f"scoped class dropped: {ops[0]}"
    assert "is-active" not in ops[0]["v"], ops[0]
    mode.set("a")
    ops = [o for w in r.watchers for o in w.refresh()]
    assert ops[0]["v"] == rendered, (ops[0], rendered)


def test_boolean_attribute_present_then_removed():
    on = signal(True)

    @mk
    def view():
        return html(t"""
            Box(hidden={on}):
                "x"
        """)

    r = render(view)
    assert " hidden" in r.body and 'hidden="' not in r.body, r.body
    on.set(False)
    assert r.watchers[0].refresh() == [
        {"op": "attr", "id": "e1", "name": "hidden", "v": None}
    ], "falsey boolean attribute must send null, not the string 'false'"


def test_conditional_swaps_branch_html():
    flag = signal(True)

    @mk
    def view():
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


def _list_view(items):
    @component
    def Item(row):
        return html(t"""
            li: {row.text}
        """)

    @mk
    def view():
        return html(t"""
            Box:
                {each(items, Item, key=(lambda r: r.id))}
        """)

    return view


def test_keyed_list_renders_with_keys():
    items = signal([Row(1, "a"), Row(2, "b")])
    r = render(_list_view(items))
    assert '<pysx-list id="0">' in r.body, r.body
    assert 'data-pysx-key="1"' in r.body and 'data-pysx-key="2"' in r.body, r.body


def test_unchanged_items_send_no_html():
    items = signal([Row(1, "a"), Row(2, "b")])
    r = render(_list_view(items))
    items.set([Row(2, "b"), Row(1, "a")])          # reorder only
    ops = r.watchers[0].refresh()
    assert len(ops) == 1 and ops[0]["op"] == "list", ops
    assert ops[0]["keys"] == ["2", "1"], ops
    assert ops[0]["html"] == {}, "a pure reorder must send zero html"


def test_only_the_changed_item_sends_html():
    items = signal([Row(1, "a"), Row(2, "b")])
    r = render(_list_view(items))
    items.set([Row(1, "a"), Row(2, "CHANGED")])
    ops = r.watchers[0].refresh()
    assert list(ops[0]["html"]) == ["2"], ops[0]["html"]
    assert "CHANGED" in ops[0]["html"]["2"]


def test_no_change_emits_nothing():
    items = signal([Row(1, "a")])
    r = render(_list_view(items))
    items.set([Row(1, "a")])
    assert r.watchers[0].refresh() == []


def test_bind_value_renders_value_and_input_hook():
    draft = signal("hi")

    @mk
    def view():
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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"{len(fns)} passed")
