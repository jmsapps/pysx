import sys

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

from pysx.parser import (  # noqa: E402
    Element, Hole, HoleKind, PysxSyntaxError, parse,
)


def counter(count, handler):
    return t"""
        Page(id="container"):
            "Count: "; {count}
            Action(type="button", onClick={handler}):
                "Increment"
    """


def test_hole_table_matches_contract():
    sk = parse(counter("C", "H").strings)
    assert sk.holes == [
        (0, HoleKind.TEXT, None),
        (1, HoleKind.EVENT, "onClick"),
    ], sk.holes


def test_no_onclick_remnant_in_statics():
    sk = parse(counter("C", "H").strings)
    texts = []

    def walk(nodes):
        for n in nodes:
            if isinstance(n, Element):
                texts.extend(v for _, v in n.attrs if isinstance(v, str))
                walk(n.children)
            elif isinstance(n, str):
                texts.append(n)

    walk(sk.root)
    joined = "".join(texts)
    assert "onClick" not in joined, joined
    assert "=" not in joined, joined


def test_tree_shape():
    sk = parse(counter("C", "H").strings)
    assert len(sk.root) == 1
    page = sk.root[0]
    assert page.tag == "Page"
    assert page.attrs == [("id", "container")]
    assert page.children[0] == "Count: "
    assert page.children[1] == Hole(0)
    action = page.children[2]
    assert action.tag == "Action"
    assert action.attrs == [("type", "button"), ("onClick", Hole(1))]
    assert action.children == ["Increment"]


def test_inline_content_after_colon():
    sk = parse(t"""
        Page:
            Action: "Go"
    """.strings)
    assert sk.root[0].children[0].children == ["Go"]


def test_rejects_quote_line_content():
    try:
        parse(t"""Page:
            "x"
        """.strings)
    except PysxSyntaxError as e:
        assert "must begin with a newline" in str(e), e
    else:
        raise AssertionError("expected PysxSyntaxError")


def test_attribute_hole_kinds():
    sk = parse(t"""
        Page(id={1}, onClick={2}, bindValue={3}):
            "x"
    """.strings)
    assert sk.holes == [
        (0, HoleKind.ATTR, "id"),
        (1, HoleKind.EVENT, "onClick"),
        (2, HoleKind.BIND, "bindValue"),
    ], sk.holes


def test_conditional_with_else():
    from pysx.parser import Conditional

    sk = parse(t"""
        Page:
            if {1}:
                Action: "yes"
            else:
                Action: "no"
    """.strings)
    assert sk.holes == [(0, HoleKind.COND, None)], sk.holes
    cond = sk.root[0].children[0]
    assert isinstance(cond, Conditional), sk.root[0].children
    assert cond.then[0].children == ["yes"]
    assert cond.otherwise[0].children == ["no"]


def test_conditional_without_else():
    from pysx.parser import Conditional

    sk = parse(t"""
        Page:
            if {1}:
                Action: "yes"
            Action: "always"
    """.strings)
    page = sk.root[0]
    cond = page.children[0]
    assert isinstance(cond, Conditional)
    assert cond.otherwise == []
    assert page.children[1].children == ["always"], page.children


def test_else_without_if_is_rejected():
    try:
        parse(t"""
            Page:
                else:
                    Action: "x"
        """.strings)
    except PysxSyntaxError as e:
        assert "without a matching" in str(e), e
    else:
        raise AssertionError("expected PysxSyntaxError")


def test_rejects_multiline_attrs():
    try:
        parse(t"""
            Page(
                id="x"):
                "y"
        """.strings)
    except PysxSyntaxError as e:
        assert "single-line" in str(e), e
    else:
        raise AssertionError("expected PysxSyntaxError")


def test_hole_in_attribute_name_position_is_rejected():
    try:
        parse(t"""
            Page({1}="x"):
                "y"
        """.strings)
    except PysxSyntaxError as e:
        assert "attribute name" in str(e), e
    else:
        raise AssertionError("expected PysxSyntaxError")


def test_hole_in_tag_position_is_rejected():
    try:
        parse(t"""
            {1}:
                "y"
        """.strings)
    except PysxSyntaxError as e:
        assert "unexpected" in str(e), e
    else:
        raise AssertionError("expected PysxSyntaxError")


def test_text_containing_braces_and_nul_is_not_a_hole():
    sk = parse(t"""
        Page:
            "a{{b}}c\x00\x00d"; {1}
    """.strings)
    assert sk.root[0].children[0] == "a{b}c\x00\x00d", sk.root[0].children[0]
    assert sk.holes == [(0, HoleKind.TEXT, None)]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"{len(fns)} passed")
