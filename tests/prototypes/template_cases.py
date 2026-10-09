"""Executable deferred/template/map proof cases.

Registered in the root architecture-proof wrapper. Cases prove the isolated
design; they do not claim the complete production grammar or static resolver.
"""

from __future__ import annotations

import gc
import weakref
from dataclasses import FrozenInstanceError, dataclass
from typing import TYPE_CHECKING, cast

import pytest

from .deferred_templates import (
    Binding,
    Element,
    Live,
    MetadataError,
    Output,
    ParsedCache,
    Scope,
    defer,
    defer2,
    evaluate,
    parse,
)
from .source_maps import (
    Position,
    Positions,
    Span,
    analyze,
    compose,
    dedent,
    positioned_tokens,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from string.templatelib import Template


@dataclass(frozen=True)
class Row:
    identity: str
    label: str
    kind: str = "ready"
    children: tuple[Row, ...] = ()


def _handler(output: Output) -> Callable[[object], str]:
    value = dict(output.attributes)["onClick"]
    assert callable(value)

    return cast("Callable[[object], str]", value)


def _capture(row: Row) -> Callable[[object], str]:
    def handler(_event: object) -> str:
        return row.identity + ":" + row.label

    return handler


def deferred_rows(_tmp_path: Path) -> None:
    item = Binding[Row]("item")
    index = Binding[int]("index")
    label = Binding[str]("label")
    rows = [Row("a", "Alpha"), Row("b", "Beta")]
    runs: list[str] = []

    def binding_text(row: Row) -> str:
        runs.append(row.identity)

        return row.label.upper()

    template = t"""
for (index,item) in {Live(lambda: list(enumerate(rows)))}
  let label = {defer(item, binding_text)}
  button title={defer2(index, label, lambda i, text: f"{i}:{text}")}
    span {defer(item, lambda row: row.identity)}{defer(label, lambda text: text)}
  button onClick={defer(item, _capture)}
"""
    namespace = {"item": item, "index": index, "label": label}
    nodes = parse(template.strings, namespace)
    assert runs == []  # constructing/parsing holes never evaluates row expressions
    initial = evaluate(nodes, template, namespace)
    assert [dict(initial[index].attributes)["title"] for index in (0, 2)] == [
        "0:ALPHA",
        "1:BETA",
    ]
    assert initial[0].children[0].text == "aALPHA"
    assert [_handler(initial[index])(None) for index in (1, 3)] == ["a:Alpha", "b:Beta"]
    rows.reverse()
    reordered = evaluate(nodes, template, namespace)
    assert [dict(reordered[index].attributes)["title"] for index in (0, 2)] == [
        "0:BETA",
        "1:ALPHA",
    ]
    assert [_handler(reordered[index])(None) for index in (1, 3)] == ["b:Beta", "a:Alpha"]
    rows[0] = Row("b", "Replacement")
    replaced = evaluate(nodes, template, namespace)
    assert dict(replaced[0].attributes)["title"] == "0:REPLACEMENT"
    assert _handler(replaced[1])(None) == "b:Replacement"
    assert _handler(initial[1])(None) == "a:Alpha"  # independent immutable original capture
    assert runs == ["a", "b", "b", "a", "b", "a"]


def nested_scopes_and_cases(_tmp_path: Path) -> None:
    item = Binding[Row]("item")
    label = Binding[str]("label")
    rows = [Row("parent", "Outer", children=(Row("child", "Inner", "waiting"),))]
    template = t"""
for item in {rows}
  let label = {defer(item, lambda row: row.label)}
  span {defer(label, lambda value: value)}
  for item in {defer(item, lambda row: row.children)}
    let label = {defer(item, lambda row: row.label)}
    match {defer(item, lambda row: row.kind)}
      case ready
        button title={defer(label, lambda value: value + " ready")}
      case waiting
        button title={defer(label, lambda value: value + " waiting")}
      case _
        button title={defer(label, lambda value: value + " fallback")}
  span {defer(item, lambda row: row.identity)}{defer(label, lambda value: value)}
"""
    namespace = {"item": item, "label": label}
    nodes = parse(template.strings, namespace)
    output = evaluate(nodes, template, namespace)
    assert output[0].text == "Outer"
    assert dict(output[1].attributes)["title"] == "Inner waiting"
    assert output[2].text == "parentOuter"
    rows[0] = Row("parent", "Changed", children=(Row("child", "New", "unknown"),))
    output = evaluate(nodes, template, namespace)
    assert dict(output[1].attributes)["title"] == "New fallback"
    assert output[2].text == "parentChanged"
    with pytest.raises(ValueError, match="explicit Binding"):
        parse(template.strings, {})
    with pytest.raises(KeyError, match="unbound"):
        Scope().get(item)
    outer = Binding[int]("same_display_name")
    inner = Binding[str]("same_display_name")
    distinct = Scope().child((outer,), (7,)).child((inner,), ("inside",))
    assert distinct.get(outer) == 7
    assert distinct.get(inner) == "inside"


class Ephemeral:
    pass


def no_frames_and_bounded_cache(_tmp_path: Path) -> None:
    def factory() -> tuple[Template, weakref.ReferenceType[Ephemeral]]:
        incidental = Ephemeral()
        binding = Binding[Row]("item")
        template = t"span {defer(binding, lambda row: row.label)}"

        return template, weakref.ref(incidental)

    template, incidental = factory()
    gc.collect()
    assert incidental() is None
    payload = Ephemeral()
    retained = weakref.ref(payload)
    carrying = t"span {payload}"
    cache = ParsedCache(entries=2, budget=1024)
    unused_namespace = {"unused": payload}
    shape = cache.get(carrying.strings, unused_namespace)
    assert cache.get(template.strings, {}) is shape
    assert isinstance(shape, tuple)
    with pytest.raises(FrozenInstanceError):
        shape[0].__setattr__("tag", "mutated")
    del carrying, payload, unused_namespace
    gc.collect()
    assert retained() is None
    cache.get(("div first",), {})
    cache.get(("div second",), {})
    assert cache.size == 2
    assert cache.used <= cache.budget
    previous_size = cache.size
    cache.get(("x" * 10000,), {})
    assert cache.size == previous_size
    assert cache.used <= cache.budget
    # Literal sentinels cannot masquerade as holes: indexes remain structural.
    tokens = positioned_tokens(("span \x00\x00", "", "{{HOLE}}"))
    assert [token.value for token in tokens if token.kind == "hole"] == ["0", "1"]
    first_binding = Binding[int]("item")
    second_binding = Binding[str]("item")
    first = t"for item in {[7]}\n  span {defer(first_binding, lambda value: value + 1)}"
    second = t"for item in {['x']}\n  span {defer(second_binding, lambda value: value.upper())}"
    assert first.strings == second.strings
    cache = ParsedCache()
    tree = cache.get(first.strings, {"item": first_binding})
    assert cache.get(second.strings, {"item": second_binding}) is tree
    assert evaluate(tree, first, {"item": first_binding})[0].text == "8"
    assert evaluate(tree, second, {"item": second_binding})[0].text == "X"
    with pytest.raises(ValueError, match="explicit Binding"):
        cache.get(first.strings, {})
    with pytest.raises(ValueError, match="explicit Binding"):
        cache.get(first.strings, {"item": Binding[int]("different")})
    with pytest.raises(ValueError, match="explicit Binding"):
        ParsedCache().get(first.strings, {"item": Binding[int]("different")})


def metadata_and_fragments(_tmp_path: Path) -> None:
    name = "é😀"
    number = 7
    template = t"span {name!r:>8}{number:03d}"
    nodes = parse(template.strings, {})
    assert len(template.strings) == len(template.interpolations) + 1
    assert template.strings[1] == ""
    assert evaluate(nodes, template, {})[0].text == f"{name!r:>8}{number:03d}"
    quoted = t'span "quoted {{literal}}"'
    assert evaluate(parse(quoted.strings, {}), quoted, {})[0].text == "quoted {literal}"
    raw = rt'span "raw \t{{literal}}"'
    assert evaluate(parse(raw.strings, {}), raw, {})[0].text == r"raw \t{literal}"
    adjacent = t"span {number}{name}"
    assembled = t"span {number}" + t"{name}"
    assert adjacent.strings == assembled.strings
    assert evaluate(parse(assembled.strings, {}), assembled, {})[0].text == "7é😀"
    live = Live(lambda: 3)
    unsupported = t"span {live!r:>4}"
    with pytest.raises(MetadataError, match="hole 0") as captured:
        evaluate(parse(unsupported.strings, {}), unsupported, {})
    assert captured.value.hole_index == 0
    attribute = t"button title={number:03d}"
    attribute_output = evaluate(parse(attribute.strings, {}), attribute, {})[0]
    assert dict(attribute_output.attributes)["title"] == "007"
    row = Binding[int]("row")
    control = t"for row in {[1]!r}\n  span {defer(row, lambda value: value)}"
    with pytest.raises(MetadataError, match="control-flow"):
        evaluate(parse(control.strings, {"row": row}), control, {"row": row})
    wrapped = t"span {defer(row, lambda _value: live)!r}"
    with pytest.raises(MetadataError, match="live"):
        evaluate(parse(wrapped.strings, {}), wrapped, {}, Scope().child((row,), (1,)))


def exact_unicode_maps(_tmp_path: Path) -> None:
    source = (
        'emoji = "😀"\nvalue = 7\ntext = t"""\n'
        "    span \\t😀 {{brace}} {value!r:>4}{value}\n"
        '    button badAttr=\\"x\\"\n"""\n'
    )
    mapped = analyze(source)[0]
    assert [fragment.text for fragment in mapped.fragments] == [
        "\n    span \t😀 {brace} ",
        "",
        '\n    button badAttr="x"\n',
    ]
    positions = Positions(source)
    hole = mapped.holes[0]
    assert source[hole.span.start : hole.span.end] == "{value!r:>4}"
    assert source[hole.expression_span.start : hole.expression_span.end] == "value"
    assert hole.conversion == ord("r")
    assert hole.format_span is not None
    assert source[hole.format_span.start : hole.format_span.end] == ":>4"
    nested_format = analyze('text = t"span {value!r:>{width}}"\n')[0].holes[0]
    assert len(nested_format.format_expressions) == 1
    expression = nested_format.format_expressions[0]
    assert 'text = t"span {value!r:>{width}}"\n'[expression.start : expression.end] == "width"
    stripped = dedent(mapped.fragments)
    assert stripped[0].text.startswith("\nspan \t😀 {brace}")
    first = stripped[0]
    escape = first.text.index("\t")
    origin = first.raw_span(escape, escape + 1)
    assert source[origin.start : origin.end] == r"\t"
    brace = first.text.index("{")
    origin = first.raw_span(brace, brace + 1)
    assert source[origin.start : origin.end] == "{{"
    last = stripped[-1]
    token = last.text.index("badAttr")
    raw_span = last.raw_span(token, token + len("badAttr"))
    start = positions.editor_position(raw_span.start)
    end = positions.editor_position(raw_span.end)
    assert source[positions.editor_offset(start) : positions.editor_offset(end)] == "badAttr"
    emoji_offset = source.index("😀", source.index("span"))
    utf16 = positions.editor_position(emoji_offset)
    assert positions.editor_position(emoji_offset + 1).utf16_column == utf16.utf16_column + 2
    with pytest.raises(ValueError, match="surrogate"):
        positions.editor_offset(Position(utf16.line, utf16.utf16_column + 1))


def crlf_tabs_multiline_and_assembly(tmp_path: Path) -> None:
    source = (
        'value = 1\r\ntext = rt"""\r\n\tspan \\t{{brace}} {value}\r\n'
        '\t\tbutton\r\n\t\t\tbadAttr="x"\r\n"""\r\n'
    )
    mapped = analyze(source)[0]
    assert mapped.fragments[0].text.startswith("\n\tspan \\t{brace} ")
    stripped = dedent(mapped.fragments)
    assert stripped[0].text.startswith("\nspan \\t{brace} ")
    assert stripped[-1].text == '\n\tbutton\n\t\tbadAttr="x"\n'
    last = stripped[-1]
    at = last.text.index("badAttr")
    raw_span = last.raw_span(at, at + 7)
    positions = Positions(source)
    assert positions.editor_position(raw_span.start) == Position(4, 3)
    assert source[raw_span.start : raw_span.end] == "badAttr"
    first = stripped[0]
    newline = first.raw_span(0, 1)
    assert source[newline.start : newline.end] == "\r\n"
    # Ordinary assembly preserves disjoint static source origins and empty holes.
    assembled_source = 'left = t"span {value}"\nright = t"{value} end"\ncombined = left + right\n'
    left, right = analyze(assembled_source)
    combined = compose(left, right)
    assert len(combined.fragments) == len(combined.holes) + 1
    assert combined.fragments[1].text == ""
    expressions = [
        assembled_source[hole.expression_span.start : hole.expression_span.end]
        for hole in combined.holes
    ]
    assert expressions == ["value", "value"]
    disjoint_source = 'left = t"abc"\nright = t"def"\n'
    left, right = analyze(disjoint_source)
    joined = compose(left, right).fragments[0]
    assert joined.text == "abcdef"
    assert [disjoint_source[s.start : s.end] for s in joined.raw_spans(0, 6)] == ["abc", "def"]
    with pytest.raises(ValueError, match="multiple raw origins"):
        joined.raw_span(0, 6)
    # Parse source containing application side effects without running them.
    marker = tmp_path / "analysis-must-not-execute"
    dangerous = (
        f'from pathlib import Path\nPath({str(marker)!r}).touch()\ntext = t"span {{unknown}}"\n'
    )
    assert len(analyze(dangerous)) == 1
    assert not marker.exists()
    assert positioned_tokens(("span ", ""))[0].start.fragment == 0
    assert Span(1, 2) == Span(1, 2)


def adjacent_nested_and_positioned_tree(_tmp_path: Path) -> None:
    adjacent_source = 'text = t"span \\t" rt" \\t {{x}}"\n'
    mapped = analyze(adjacent_source)[0]
    assert mapped.fragments[0].text == "span \t \\t {x}"
    fragments = mapped.fragments[0]
    decoded_tab = fragments.text.index("\t")
    origin = fragments.raw_span(decoded_tab, decoded_tab + 1)
    assert adjacent_source[origin.start : origin.end] == r"\t"
    literal_backslash = fragments.text.index("\\")
    origin = fragments.raw_span(literal_backslash, literal_backslash + 1)
    assert adjacent_source[origin.start : origin.end] == "\\"
    assert len(fragments.raw_spans(0, len(fragments.text))) == 2
    escaped_quotes = analyze('text = t"span \\"quoted\\""\n')[0]
    assert escaped_quotes.fragments[0].text == 'span "quoted"'
    nested_source = "text = t\"span {t'inner {value}'}\"\n"
    outer, inner = analyze(nested_source)
    assert outer.fragments[0].text == "span "
    assert inner.fragments[0].text == "inner "
    expression = outer.holes[0].expression_span
    assert nested_source[expression.start : expression.end] == "t'inner {value}'"
    dictionary_source = "text = t\"span {mapping['key']} {{x}}\"\n"
    dictionary = analyze(dictionary_source)[0]
    expression = dictionary.holes[0].expression_span
    assert dictionary_source[expression.start : expression.end] == "mapping['key']"
    source = (
        'value = 1\nmarkup = t"""\n    div\n      span {value}\n      button badAttr={value}\n"""\n'
    )
    mapped = analyze(source)[0]
    normalized = dedent(mapped.fragments)
    tree = parse(tuple(fragment.text for fragment in normalized), {})
    root = tree[0]
    assert isinstance(root, Element)
    assert root.tag == "div"
    tags = [child.tag for child in root.children if isinstance(child, Element)]
    assert tags == ["span", "button"]

    for node in (root, *root.children):
        assert isinstance(node, Element)
        origin = normalized[node.span.start.fragment].origins[node.span.start.offset]
        assert source[origin.start : origin.start + len(node.tag)] == node.tag
        positions = Positions(source)
        start = positions.editor_position(origin.start)
        end = positions.editor_position(origin.start + len(node.tag))
        assert source[positions.editor_offset(start) : positions.editor_offset(end)] == node.tag
    assert root.span.end == root.children[-1].span.end
    with pytest.raises(FrozenInstanceError):
        root.__setattr__("tag", "mutated")


CASES: dict[str, Callable[[Path], None]] = {
    "deferred_rows_reorder_replacement": deferred_rows,
    "nested_scopes_case_attributes": nested_scopes_and_cases,
    "no_frames_immutable_bounded_cache": no_frames_and_bounded_cache,
    "template_metadata_adjacent_raw": metadata_and_fragments,
    "exact_unicode_source_maps": exact_unicode_maps,
    "crlf_tabs_multiline_assembled_maps": crlf_tabs_multiline_and_assembly,
    "adjacent_nested_positioned_ast": adjacent_nested_and_positioned_tree,
}
