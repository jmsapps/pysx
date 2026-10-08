"""Independent ownership, rejection and coordinate assertions for inline rows."""

import json
from pathlib import Path
from typing import cast

import pytest

from pysx.parser import Element, Hole, HoleKind, Node, PysxSyntaxError, parse

MATRIX = Path(__file__).parent / "fixtures" / "inline_siblings.json"


def _cases(kind: str) -> list[dict[str, object]]:
    matrix = cast("dict[str, list[dict[str, object]]]", json.loads(MATRIX.read_text()))
    assert matrix[kind]

    return matrix[kind]


def _shape(nodes: tuple[Node, ...]) -> list[object]:
    result: list[object] = []

    for node in nodes:
        if isinstance(node, Element):
            result.append(
                [
                    node.tag.index if isinstance(node.tag, Hole) else node.tag,
                    _shape(node.children),
                ]
            )
        elif isinstance(node, Hole):
            result.append(node.index)
        elif isinstance(node, str):
            result.append(str(node))
        else:
            raise AssertionError(f"unexpected matrix node {type(node).__name__}")

    return result


@pytest.mark.parametrize("case", _cases("valid"), ids=lambda case: repr(case["fragments"]))
def test_inline_sibling_ownership_matrix(case: dict[str, object]) -> None:
    skeleton = parse(tuple(cast("list[str]", case["fragments"])))
    assert _shape(skeleton.root) == case["tree"]
    assert [kind.value for _, kind, _ in skeleton.holes] == case["kinds"]


@pytest.mark.parametrize("case", _cases("invalid"), ids=lambda case: repr(case["fragments"]))
def test_inline_sibling_rejection_matrix(case: dict[str, object]) -> None:
    strings = tuple(cast("list[str]", case["fragments"]))

    with pytest.raises(PysxSyntaxError, match=str(case["reason"])) as caught:
        parse(strings)
    position = caught.value.position
    assert position is not None

    if case["token"] in {"span", "Card", "if", "let"} and len(strings[0]) > 4:
        assert strings[position.fragment][position.offset :].startswith(str(case["token"]))


@pytest.mark.parametrize("header", ["br", "h2: 'x'; br", "br; 'tail'", "'prefix'; Card()"])
@pytest.mark.parametrize("empty", ["\n", "    ;;;\n", "  ;\n", ";;\n", "\n;\n\n"])
def test_inline_forbidden_body_survives_empty_rows(header: str, empty: str) -> None:
    source = f"\nCard:\n  {header}\n{empty}    span: 'bad'"

    with pytest.raises(PysxSyntaxError, match=r"colon|own row") as caught:
        parse((source,))
    position = caught.value.position
    assert position is not None
    assert source[position.offset :].startswith("span")


def test_inline_sibling_exact_spans_and_attribute_kinds() -> None:
    strings = ('h2: "😀"; Button(onClick=', ", bindValue=", ", ref=", ", class=", "); br")
    skeleton = parse(strings)
    assert skeleton.holes == (
        (0, HoleKind.EVENT, "onClick"),
        (1, HoleKind.BIND, "bindValue"),
        (2, HoleKind.ATTR, "ref"),
        (3, HoleKind.ATTR, "class"),
    )
    first, button, last = skeleton.root
    assert isinstance(first, Element)
    assert first.span is not None
    assert strings[0][first.span.start.offset : first.span.end.offset] == 'h2: "😀"'
    assert isinstance(button, Element)
    assert button.span is not None
    assert (button.span.start.fragment, button.span.start.offset) == (0, 9)
    assert (button.span.end.fragment, button.span.end.offset) == (4, 1)
    assert isinstance(last, Element)
    assert last.span is not None
    assert (last.span.start.fragment, last.span.start.offset) == (4, 3)
    assert (last.span.end.fragment, last.span.end.offset) == (4, 5)


def test_inline_namespace_is_local_to_each_sibling() -> None:
    skeleton = parse(
        ("\nsvg:\n  circle; rect\n  foreignObject:\n    div; span\nmath:\n  mi; mo\nsvg; div",)
    )
    svg, math, sibling_svg, div = skeleton.root
    assert isinstance(svg, Element)
    assert isinstance(math, Element)
    assert isinstance(sibling_svg, Element)
    assert isinstance(div, Element)
    assert [node.namespace for node in svg.children if isinstance(node, Element)] == [
        "svg",
        "svg",
        "svg",
    ]
    foreign = svg.children[2]
    assert isinstance(foreign, Element)
    assert [node.namespace for node in foreign.children if isinstance(node, Element)] == [
        "html",
        "html",
    ]
    assert [node.namespace for node in math.children if isinstance(node, Element)] == [
        "math",
        "math",
    ]
    assert sibling_svg.namespace == "svg"
    assert div.namespace == "html"


@pytest.mark.parametrize("tag", ["Panel", "lowercase", "svg", "math", "my-element"])
@pytest.mark.parametrize("suffix", ["", ";", ";;;"])
def test_inline_element_only_matches_independent_multiline_tree(tag: str, suffix: str) -> None:
    inline = parse((f"{tag}(id='x'); span: 'text'; br{suffix}",))
    multiline = parse((f"\n{tag}(id='x')\nspan: 'text'\nbr()",))
    assert _shape(inline.root) == _shape(multiline.root)
    assert inline.holes == multiline.holes
    assert [node.attrs for node in inline.root if isinstance(node, Element)] == [
        node.attrs for node in multiline.root if isinstance(node, Element)
    ]


def test_inline_cache_ignores_interpolation_values_and_empty_fragments() -> None:
    from pysx import pysx, render, signal

    value = signal("quotes;\n'newlines'")
    template = t"p: {value}; br; span: {value!s:>8}"
    skeleton = parse(template.strings)
    assert parse(template.strings) is skeleton
    result = render(lambda: pysx(template))
    value.set("updated;")
    ops = [op for watcher in result.watchers for op in watcher.refresh()]
    assert len(ops) == 2
    assert parse(template.strings) is skeleton
    result.dispose()


def test_inline_quote_escapes_multiline_and_nul_remain_content() -> None:
    source = "\np: 'a\\'b;\\\\c\n{literal}\x00'; br"
    assert _shape(parse((source,)).root) == [["p", ["a'b;\\c\n{literal}\x00"]], ["br", []]]


def test_inline_controls_keep_branch_contiguity_and_parent_checks() -> None:
    skeleton = parse(("\nif ", ":\n  p: 'a'; br\nelse:\n  div; br"))
    assert skeleton.holes == ((0, HoleKind.COND, None),)

    with pytest.raises(PysxSyntaxError, match="contiguous"):
        parse(("\nif ", ":\n  br\nbr; br\nelse:\n  br"))

    with pytest.raises(PysxSyntaxError, match="directly inside match"):
        parse(("\ndiv:\n  case 'x':\n    br",))


def test_inline_spans_exclude_separators_and_surrounding_whitespace() -> None:
    source = 'p: "x"  ;;  br  ; span: "tail" ;;;'
    skeleton = parse((source,))
    slices: list[str] = []

    for node in skeleton.root:
        assert isinstance(node, Element)
        assert node.span is not None
        slices.append(source[node.span.start.offset : node.span.end.offset])
    assert slices == ['p: "x"', "br", 'span: "tail"']


@pytest.mark.parametrize("header", ["br", "h2: 'x'; br", "br; 'tail'"])
def test_dedented_separator_cannot_reparent_equal_indent_sibling(header: str) -> None:
    source = f"\nCard:\n    {header}\n;\n    span: 'bad'"

    with pytest.raises(PysxSyntaxError, match=r"colon|own row") as caught:
        parse((source,))
    assert caught.value.position is not None
    assert source[caught.value.position.offset :].startswith("span")
