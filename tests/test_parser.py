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


def test_positioned_multiline_coordinates_immutable_structure_and_cache() -> None:
    from dataclasses import FrozenInstanceError

    from pysx import native
    from pysx.parser import LiteralText, Position

    template = t"""
        {native.Div}(
            title={"😀"},
            id='positioned'
        ):
            span: "child"
    """
    skeleton = parse(template.strings)
    assert skeleton is parse(template.strings)
    assert skeleton.strings is template.strings
    element = skeleton.root[0]
    assert isinstance(element, Element)
    assert isinstance(element.tag, Hole)
    assert element.span is not None
    assert element.span.start == element.tag.position
    assert element.span.start == Position(0, template.strings[0].index("\n") + 9, 1, 8)
    attr = element.attrs[0][1]
    assert isinstance(attr, Hole)
    assert attr.position is not None
    assert attr.position.line == 2
    assert template.strings[attr.position.fragment][attr.position.offset :] == ""

    with pytest.raises(FrozenInstanceError):
        element.__setattr__("tag", "changed")
    assert isinstance(element.children, tuple)
    literal = element.attrs[1][1]
    assert isinstance(literal, LiteralText)
    assert literal.span.start.line == literal.span.end.line == 3
    assert element.span.end.line == 5
    with pytest.raises(AttributeError, match="immutable"):
        literal.__setattr__("_span", element.span)
    assert parse.cache_info().maxsize == 256


@pytest.mark.parametrize(
    ("strings", "expected"),
    [
        (("\ndiv(\n  id=oops\n):",), (2, 5)),
        (("\n\tdiv:",), (1, 0)),
        (("\n  else:",), (1, 2)),
    ],
)
def test_positioned_multiline_errors(strings: tuple[str, ...], expected: tuple[int, int]) -> None:
    with pytest.raises(PysxSyntaxError) as caught:
        parse(strings)
    position = caught.value.position
    assert position is not None
    assert (position.line, position.column) == expected


@pytest.mark.parametrize("strings", [("\n" + "a" * 1_048_576,), ("",) * 16386])
def test_positioned_multiline_cache_input_bounds(strings: tuple[str, ...]) -> None:
    with pytest.raises(PysxSyntaxError, match="exceeds"):
        parse(strings)


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
    assert sk.holes == ((0, HoleKind.TEXT, None), (1, HoleKind.EVENT, "onClick")), sk.holes


def test_no_onclick_remnant_in_statics() -> None:
    sk = parse(counter("C", "H").strings)
    texts: list[str] = []

    def walk(nodes: tuple[Node, ...]) -> None:
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
    assert page.attrs == (("id", "container"),)
    assert page.children[0] == "Count: "
    assert page.children[1] == Hole(0)
    action = page.children[2]
    assert isinstance(action, Element)
    assert action.tag == "Action"
    assert action.attrs == (("type", "button"), ("onClick", Hole(1)))
    assert action.children == ("Increment",)


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
    assert action.children == ("Go",)


def test_rejects_quote_line_content() -> None:
    with pytest.raises(PysxSyntaxError, match="must begin with a newline"):
        parse(
            t"""Page:
            "x"
        """.strings
        )


def test_single_line_markup_and_positioned_attribute_names() -> None:
    from pysx.parser import LiteralText

    skeleton = parse(t'''Panel(title="Hello"): "Content"'''.strings)
    panel = skeleton.root[0]
    assert isinstance(panel, Element)
    name = panel.attrs[0][0]
    assert isinstance(name, LiteralText)
    assert name.span.start.offset == 6
    assert name.span.end.offset == 11


def test_attribute_hole_kinds() -> None:
    sk = parse(
        t"""
        Page(id={1}, onClick={2}, bindValue={3}):
            "x"
    """.strings
    )
    assert sk.holes == (
        (0, HoleKind.ATTR, "id"),
        (1, HoleKind.EVENT, "onClick"),
        (2, HoleKind.BIND, "bindValue"),
    ), sk.holes


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
    assert sk.holes == ((0, HoleKind.COND, None),), sk.holes
    page = sk.root[0]
    assert isinstance(page, Element)
    cond = page.children[0]
    assert isinstance(cond, Conditional), page.children
    assert isinstance(cond.then[0], Element)
    assert cond.then[0].children == ("yes",)
    assert isinstance(cond.otherwise[0], Element)
    assert cond.otherwise[0].children == ("no",)


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
    assert cond.otherwise == ()
    assert isinstance(page.children[1], Element)
    assert page.children[1].children == ("always",), page.children


def test_else_without_if_is_rejected() -> None:
    with pytest.raises(PysxSyntaxError, match="without a matching"):
        parse(
            t"""
            Page:
                else:
                    Action: "x"
        """.strings
        )


def test_positioned_multiline_attrs() -> None:
    skeleton = parse(
        t"""
        Page(
            id="x"):
            "y"
    """.strings
    )
    page = skeleton.root[0]
    assert isinstance(page, Element)
    assert page.attrs == (("id", "x"),)
    assert page.children == ("y",)


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
    assert skeleton.holes == ((0, HoleKind.TAG, None),)


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
    assert sk.holes == ((0, HoleKind.TEXT, None),)


@pytest.mark.parametrize("tag", ["br", "div", "Break", "lowercase", "_custom", "my-element"])
def test_bare_childless_name(tag: str) -> None:
    node = parse((tag,)).root[0]
    assert isinstance(node, Element)
    assert node.tag == tag
    assert node.children == ()
    assert node.namespace == "html"


@pytest.mark.parametrize("separator", ["", "\n", "  ;;;\n", ";\n", "    ;;\n"])
def test_bare_childless_forbids_body_across_empty_rows(separator: str) -> None:
    strings = (f"\nCard:\n  div\n{separator}    span: 'child'",)

    with pytest.raises(PysxSyntaxError, match="add a colon: div:") as caught:
        parse(strings)
    position = caught.value.position
    assert position is not None
    assert strings[position.fragment][position.offset :].startswith("span")


def test_bare_childless_dedent_resolves_forbidden_body() -> None:
    skeleton = parse(("\nCard:\n  br\n  div:\n    span: 'child'",))
    card = skeleton.root[0]
    assert isinstance(card, Element)
    assert len(card.children) == 2
    div = card.children[1]
    assert isinstance(div, Element)
    assert len(div.children) == 1
