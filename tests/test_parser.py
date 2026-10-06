from typing import TYPE_CHECKING

import pytest

from pysx.parser import (
    Element,
    Hole,
    HoleKind,
    Node,
    PysxSyntaxError,
    parse,
)

if TYPE_CHECKING:
    from string.templatelib import Template


def counter(count: object, handler: object) -> Template:

    return t"""
        Page(id="container"):
            "Count: "; {count}
            Action(type="button", onClick={handler}):
                "Increment"
    """


def test_hole_table_matches_contract() -> None:
    sk = parse(counter("C", "H").strings)
    assert sk.holes == [
        (0, HoleKind.TEXT, None),
        (1, HoleKind.EVENT, "onClick"),
    ], sk.holes


def test_no_onclick_remnant_in_statics() -> None:
    sk = parse(counter("C", "H").strings)
    texts: list[str] = []

    def walk(nodes: list[Node]) -> None:
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


def test_tree_shape() -> None:
    sk = parse(counter("C", "H").strings)
    assert len(sk.root) == 1
    page = sk.root[0]
    assert isinstance(page, Element)
    assert page.tag == "Page"
    assert page.attrs == [("id", "container")]
    assert page.children[0] == "Count: "
    assert page.children[1] == Hole(0)
    action = page.children[2]
    assert isinstance(action, Element)
    assert action.tag == "Action"
    assert action.attrs == [("type", "button"), ("onClick", Hole(1))]
    assert action.children == ["Increment"]


def test_inline_content_after_colon() -> None:
    sk = parse(
        t"""
        Page:
            Action: "Go"
    """.strings
    )
    page = sk.root[0]
    assert isinstance(page, Element)
    action = page.children[0]
    assert isinstance(action, Element)
    assert action.children == ["Go"]


def test_rejects_quote_line_content() -> None:
    with pytest.raises(PysxSyntaxError, match="must begin with a newline"):
        parse(
            t"""Page:
            "x"
        """.strings
        )


def test_attribute_hole_kinds() -> None:
    sk = parse(
        t"""
        Page(id={1}, onClick={2}, bindValue={3}):
            "x"
    """.strings
    )
    assert sk.holes == [
        (0, HoleKind.ATTR, "id"),
        (1, HoleKind.EVENT, "onClick"),
        (2, HoleKind.BIND, "bindValue"),
    ], sk.holes


def test_conditional_with_else() -> None:
    from pysx.parser import Conditional

    sk = parse(
        t"""
        Page:
            if {1}:
                Action: "yes"
            else:
                Action: "no"
    """.strings
    )
    assert sk.holes == [(0, HoleKind.COND, None)], sk.holes
    page = sk.root[0]
    assert isinstance(page, Element)
    cond = page.children[0]
    assert isinstance(cond, Conditional), page.children
    assert isinstance(cond.then[0], Element)
    assert cond.then[0].children == ["yes"]
    assert isinstance(cond.otherwise[0], Element)
    assert cond.otherwise[0].children == ["no"]


def test_conditional_without_else() -> None:
    from pysx.parser import Conditional

    sk = parse(
        t"""
        Page:
            if {1}:
                Action: "yes"
            Action: "always"
    """.strings
    )
    page = sk.root[0]
    assert isinstance(page, Element)
    cond = page.children[0]
    assert isinstance(cond, Conditional)
    assert cond.otherwise == []
    assert isinstance(page.children[1], Element)
    assert page.children[1].children == ["always"], page.children


def test_else_without_if_is_rejected() -> None:
    with pytest.raises(PysxSyntaxError, match="without a matching"):
        parse(
            t"""
            Page:
                else:
                    Action: "x"
        """.strings
        )


def test_rejects_multiline_attrs() -> None:
    with pytest.raises(PysxSyntaxError, match="single-line"):
        parse(
            t"""
            Page(
                id="x"):
                "y"
        """.strings
        )


def test_hole_in_attribute_name_position_is_rejected() -> None:
    with pytest.raises(PysxSyntaxError, match="attribute name"):
        parse(
            t"""
            Page({1}="x"):
                "y"
        """.strings
        )


def test_component_tags_hole_in_tag_position() -> None:
    skeleton = parse(
        t"""
        {1}:
            "y"
    """.strings
    )
    assert skeleton.holes == [(0, HoleKind.TAG, None)]


def test_text_containing_braces_and_nul_is_not_a_hole() -> None:
    sk = parse(
        t"""
        Page:
            "a{{b}}c\x00\x00d"; {1}
    """.strings
    )
    page = sk.root[0]
    assert isinstance(page, Element)
    assert page.children[0] == "a{b}c\x00\x00d", page.children[0]
    assert sk.holes == [(0, HoleKind.TEXT, None)]
