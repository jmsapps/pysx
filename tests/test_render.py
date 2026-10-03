import sys
from html.parser import HTMLParser

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0] + "/src")

from pysx import render, signal  # noqa: E402
from pysx.examples.counter.app import app  # noqa: E402


class _Attrs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    @classmethod
    def of(cls, markup):
        p = cls()
        p.feed(markup)
        return p.tags


def test_button_carries_real_data_attribute():
    r = render(app)
    tags = _Attrs.of(r.body)
    buttons = [a for t, a in tags if t == "button"]
    assert len(buttons) == 1, tags
    attrs = buttons[0]
    assert attrs.get("data-pysx-click") == "h1", attrs
    assert "onclick" not in attrs, attrs
    assert attrs.get("type") == "button", attrs


def test_styled_class_applied_and_each_rule_defined_once():
    r = render(app)
    tags = _Attrs.of(r.body)
    used = {a["class"] for _, a in tags if "class" in a}
    assert len(used) == 2, used                      # Page and Action
    for cls in used:
        assert cls.startswith("pysx-"), cls
        assert r.css.count(f".{cls} {{") == 1, cls   # defined exactly once


def test_identical_css_collapses_to_one_class():
    from pysx import div, styled, stylesheet

    a = styled(div, t"""color: rebeccapurple;""")
    b = styled(div, t"""color: rebeccapurple;""")
    assert a.css_class == b.css_class, (a, b)
    assert stylesheet().count(f".{a.css_class} {{") == 1


def test_slot_prefilled():
    r = render(app)
    assert '<pysx-slot id="0">0</pysx-slot>' in r.body, r.body


def test_handler_registered_and_watcher_emits():
    r = render(app)
    assert [type(w).__name__ for w in r.watchers] == ["TextWatcher"]
    r.handlers["h1"](None)
    assert r.watchers[0].refresh() == [{"op": "text", "id": "0", "v": "1"}]
    assert r.watchers[0].refresh() == [], "unchanged value must emit nothing"


def test_sessions_do_not_share_state():
    a, b = render(app), render(app)
    a.handlers["h1"](None)
    a.handlers["h1"](None)
    assert a.watchers[0].refresh() == [{"op": "text", "id": "0", "v": "2"}]
    assert b.watchers[0].refresh() == [], "module-scope signal leaked across renders"


def test_hole_value_is_escaped():
    from pysx import component, div, html, styled

    Box = styled(div, t"""color: red;""")
    payload = signal("<script>alert(1)</script>")

    ns = {"Box": Box}

    @component
    def view():
        return html(t"""
            Box:
                {payload}
        """)

    view.__globals__.update(ns)
    r = render(view)
    assert "<script>" not in r.body, r.body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.body, r.body


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"{len(fns)} passed")
