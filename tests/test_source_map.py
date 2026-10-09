"""Exact maps preserve raw syntax, Python byte columns and editor UTF-16."""

import ast

import pytest

from pysx.source_map import EditorPosition, LiteralMapper, Positions, SourceSpan, compose


@pytest.mark.parametrize(
    "literal",
    [
        r'''t"\tPanel: '😀 {{text}}' {value!r:>{width}}"''',
        r'''rt"Panel: '\t{{text}}' {value}"''',
        '''t"Panel: " t"'text' {value}"''',
        r'''t"\n  Panel: " t"{value}\n"''',
        '''t"{pysx(t'Panel: {value}')}"''',
        '''t"{value}{other}"''',
        '''t"{value=}"''',
        '''t"""\r\n\tPanel: {value}\r\n"""''',
    ],
)
def test_exact_source_literal_maps(literal: str) -> None:
    source = "emoji = '😀'; result = " + literal
    tree = ast.parse(source)
    mapper = LiteralMapper(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.TemplateStr):
            continue
        mapped = mapper.template(node)
        expected = [""]

        for value in node.values:
            if isinstance(value, ast.Constant):
                expected[-1] += str(value.value)
            else:
                expected.append("")
        assert mapped.strings == tuple(expected)

        for fragment in mapped.fragments:
            assert len(fragment.origins) == len(fragment.text)

            for origin in fragment.origins:
                assert origin is not None
                assert source[origin.start : origin.end]
                position = mapper.positions.editor_position(origin.start)
                assert mapper.positions.editor_offset(position) == origin.start


def test_exact_source_decoded_escape_and_brace_ranges() -> None:
    source = r'''view = t"\t{{😀}}"'''
    node = ast.parse(source).body[0]
    assert isinstance(node, ast.Assign)
    assert isinstance(node.value, ast.TemplateStr)
    text = LiteralMapper(source).template(node.value).fragments[0]
    assert text.text == "\t{😀}"
    assert source[text.span(0, 1).start : text.span(0, 1).end] == r"\t"
    assert source[text.span(1, 2).start : text.span(1, 2).end] == "{{"
    assert source[text.span(3, 4).start : text.span(3, 4).end] == "}}"


def test_exact_source_assembled_disjoint_edit_rejected() -> None:
    source = '''view = t"Pa" + t"nel: 'text'"'''
    nodes = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.TemplateStr)]
    mapper = LiteralMapper(source)
    joined = compose(mapper.template(nodes[0]), mapper.template(nodes[1]))
    assert joined.strings == ("Panel: 'text'",)
    assert len(joined.fragments[0].spans(0, 5)) == 2

    with pytest.raises(ValueError, match="disjoint"):
        joined.fragments[0].span(0, 5)


def test_exact_source_utf8_utf16_and_invalid_surrogate_boundary() -> None:
    positions = Positions("😀 name\n")
    assert positions.ast_offset(1, 5) == 2
    assert positions.editor_position(2) == EditorPosition(0, 3)
    assert positions.editor_offset(EditorPosition(1, 0)) == 7

    with pytest.raises(ValueError, match="surrogate"):
        positions.editor_offset(EditorPosition(0, 1))
    assert SourceSpan(0, 1).start == 0
