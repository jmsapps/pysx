"""Static positioned-template/source-map and bounded-cache feasibility proof.

No application imports, interpolation evaluation or frame inspection. This is a
restricted complete-source proof, not an incomplete-buffer recovery engine.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class Span:
    start: int
    end: int


@dataclass(frozen=True)
class Position:
    line: int
    utf16_column: int


@dataclass(frozen=True)
class Positions:
    source: str

    def text_offset(self, line: int, column: int) -> int:
        lines = self.source.splitlines(keepends=True)

        return sum(len(value) for value in lines[: line - 1]) + column

    def ast_offset(self, line: int, byte_column: int) -> int:
        lines = self.source.splitlines(keepends=True)
        prefix = sum(len(value) for value in lines[: line - 1])
        decoded_prefix = lines[line - 1].encode("utf-8")[:byte_column].decode("utf-8")

        return prefix + len(decoded_prefix)

    def ast_span(self, node: ast.AST) -> Span:
        if not isinstance(node, (ast.expr, ast.stmt)):
            raise TypeError("node has no source coordinates")

        if node.end_lineno is None or node.end_col_offset is None:
            raise ValueError("missing AST end coordinates")

        return Span(
            self.ast_offset(node.lineno, node.col_offset),
            self.ast_offset(node.end_lineno, node.end_col_offset),
        )

    def editor_position(self, offset: int) -> Position:
        if not 0 <= offset <= len(self.source):
            raise ValueError("offset outside original source")
        preceding = self.source[:offset]
        row = preceding.count("\n")
        line_prefix = preceding.rsplit("\n", 1)[-1]

        return Position(row, len(line_prefix.encode("utf-16-le")) // 2)

    def editor_offset(self, position: Position) -> int:
        lines = self.source.splitlines(keepends=True)

        if self.source.endswith("\n"):
            lines.append("")

        if not 0 <= position.line < len(lines):
            raise ValueError("line outside source")
        line = lines[position.line]
        units = 0

        for index, character in enumerate(line):
            if units == position.utf16_column:
                return sum(len(value) for value in lines[: position.line]) + index
            units += len(character.encode("utf-16-le")) // 2

            if units > position.utf16_column:
                raise ValueError("UTF16 coordinate splits surrogate pair")

        if units == position.utf16_column:
            return sum(len(value) for value in lines[: position.line + 1])

        raise ValueError("UTF16 column outside source")


@dataclass(frozen=True)
class MappedText:
    text: str
    origins: tuple[Span, ...]

    def raw_span(self, start: int, end: int) -> Span:
        spans = self.raw_spans(start, end)

        if len(spans) != 1:
            raise ValueError("assembled range has multiple raw origins")

        return spans[0]

    def raw_spans(self, start: int, end: int) -> tuple[Span, ...]:
        if not 0 <= start < end <= len(self.text):
            raise ValueError("expected a nonempty mapped character range")
        result: list[Span] = []

        for span in self.origins[start:end]:
            if result and result[-1].end >= span.start >= result[-1].start:
                result[-1] = Span(result[-1].start, max(span.end, result[-1].end))
            else:
                result.append(span)

        return tuple(result)


_ESCAPE = re.compile(
    r"\\(?:N\{[^}]+\}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2}|[0-7]{1,3}|\r\n|\n|.)",
    re.DOTALL,
)


def _decode(raw: str, origin: int, *, raw_literal: bool) -> MappedText:
    characters: list[str] = []
    spans: list[Span] = []
    cursor = 0

    while cursor < len(raw):
        width = 1
        character = raw[cursor]

        if raw.startswith("\r\n", cursor):
            character = "\n"
            width = 2
        elif raw.startswith(("{{", "}}"), cursor):
            width = 2
        elif character == "\\" and not raw_literal:
            match = _ESCAPE.match(raw, cursor)

            if match is None:
                raise ValueError("incomplete static escape")
            token = match.group(0)
            width = len(token)
            # Only a single literal escape is parsed, never an application expression.
            value = ast.literal_eval('"' + token + '"')

            if not isinstance(value, str):
                raise TypeError("expected decoded literal text")
            character = value
        characters.extend(character)
        spans.extend(Span(origin + cursor, origin + cursor + width) for _ in character)
        cursor += width

    return MappedText("".join(characters), tuple(spans))


@dataclass(frozen=True)
class HoleSource:
    span: Span
    expression_span: Span
    expression: str
    conversion: int
    format_span: Span | None
    format_expressions: tuple[Span, ...]


@dataclass(frozen=True)
class TemplateSource:
    span: Span
    fragments: tuple[MappedText, ...]
    holes: tuple[HoleSource, ...]


def compose(first: TemplateSource, second: TemplateSource) -> TemplateSource:
    """Statically known Template addition preserves each literal's provenance."""
    joined = MappedText(
        first.fragments[-1].text + second.fragments[0].text,
        first.fragments[-1].origins + second.fragments[0].origins,
    )

    return TemplateSource(
        Span(min(first.span.start, second.span.start), max(first.span.end, second.span.end)),
        (*first.fragments[:-1], joined, *second.fragments[1:]),
        (*first.holes, *second.holes),
    )


def analyze(source: str) -> tuple[TemplateSource, ...]:
    """Build maps from the original source and Python's static AST only."""
    tree = ast.parse(source)
    positions = Positions(source)
    # AST Constants may merge adjacent literals with different raw prefixes.
    # Token delimiters retain the actual content windows and original raw braces.
    starts: list[tuple[int, bool]] = []
    literal_windows: list[tuple[Span, bool]] = []

    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.TSTRING_START:
            starts.append((positions.text_offset(*token.end), "r" in token.string.lower()))
        elif token.type == tokenize.TSTRING_END:
            start, raw = starts.pop()
            literal_windows.append((Span(start, positions.text_offset(*token.start)), raw))
    result: list[TemplateSource] = []
    templates = sorted(
        (node for node in ast.walk(tree) if isinstance(node, ast.TemplateStr)),
        key=lambda node: (node.lineno, node.col_offset),
    )

    for template in templates:
        template_span = positions.ast_span(template)
        fragments: list[MappedText] = []
        holes: list[HoleSource] = []
        pending_text = ""
        pending_origins: tuple[Span, ...] = ()

        for node in template.values:
            if isinstance(node, ast.Constant):
                if not isinstance(node.value, str):
                    raise TypeError("template constant is not text")
                span = positions.ast_span(node)
                mapped_parts: list[MappedText] = []

                for window, is_raw in sorted(literal_windows, key=lambda pair: pair[0].start):
                    if window.start < template_span.start or window.end > template_span.end:
                        continue
                    start = max(span.start, window.start)
                    end = min(span.end, window.end)

                    if start < end:
                        mapped_parts.append(_decode(source[start:end], start, raw_literal=is_raw))
                mapped_text = "".join(part.text for part in mapped_parts)

                if mapped_text != node.value:
                    # Dynamic construction/debug-expression source needs separate policy.
                    raise ValueError("unsupported static literal mapping; no invented positions")
                pending_text += mapped_text
                pending_origins += tuple(span for part in mapped_parts for span in part.origins)
            elif isinstance(node, ast.Interpolation):
                fragments.append(MappedText(pending_text, pending_origins))
                pending_text = ""
                pending_origins = ()
                holes.append(
                    HoleSource(
                        positions.ast_span(node),
                        positions.ast_span(node.value),
                        node.str or "",
                        node.conversion,
                        positions.ast_span(node.format_spec) if node.format_spec else None,
                        tuple(
                            positions.ast_span(expression.value)
                            for expression in ast.walk(node.format_spec)
                            if isinstance(expression, ast.FormattedValue)
                        )
                        if node.format_spec
                        else (),
                    )
                )
            else:
                raise TypeError("unexpected template AST value")
        fragments.append(MappedText(pending_text, pending_origins))
        result.append(TemplateSource(template_span, tuple(fragments), tuple(holes)))

    return tuple(result)


def dedent(fragments: tuple[MappedText, ...]) -> tuple[MappedText, ...]:
    """Out-of-band common prefix; continuation and empty-hole structure retained."""
    starts: list[str] = []

    for index, fragment in enumerate(fragments):
        parts = fragment.text.split("\n")
        starts.extend(parts if index == 0 else parts[1:])
    indents = [line[: len(line) - len(line.lstrip(" \t"))] for line in starts if line.strip()]
    margin = indents[0] if indents else ""

    for indent in indents[1:]:
        common = 0

        for first, second in zip(margin, indent, strict=False):
            if first != second:
                break
            common += 1
        margin = margin[:common]
    result: list[MappedText] = []

    for fragment_index, fragment in enumerate(fragments):
        text: list[str] = []
        origins: list[Span] = []
        offset = 0
        parts = fragment.text.split("\n")

        for line_index, line in enumerate(parts):
            is_start = fragment_index == 0 or line_index > 0
            continues = fragment_index < len(fragments) - 1 and line_index == len(parts) - 1
            remove = 0

            if is_start:
                if not line.strip() and not continues:
                    remove = len(line)
                elif line.startswith(margin):
                    remove = len(margin)
            text.extend(line[remove:])
            origins.extend(fragment.origins[offset + remove : offset + len(line)])
            offset += len(line)

            if line_index < len(parts) - 1:
                text.append("\n")
                origins.append(fragment.origins[offset])
                offset += 1
        result.append(MappedText("".join(text), tuple(origins)))

    return tuple(result)


@dataclass(frozen=True)
class TemplateCoordinate:
    fragment: int
    offset: int


@dataclass(frozen=True)
class PositionedToken:
    kind: str
    value: str
    start: TemplateCoordinate
    end: TemplateCoordinate


def positioned_tokens(strings: tuple[str, ...]) -> tuple[PositionedToken, ...]:
    """One immutable coordinate model; raw/editor provenance is attached separately."""
    result: list[PositionedToken] = []

    for fragment_index, fragment in enumerate(strings):
        for match in re.finditer(r"[^\s]+", fragment):
            result.append(
                PositionedToken(
                    "text",
                    match.group(0),
                    TemplateCoordinate(fragment_index, match.start()),
                    TemplateCoordinate(fragment_index, match.end()),
                )
            )

        if fragment_index < len(strings) - 1:
            coordinate = TemplateCoordinate(fragment_index, len(fragment))
            result.append(PositionedToken("hole", str(fragment_index), coordinate, coordinate))

    return tuple(result)


class ShapeCache:
    """Bounded logical footprint; measured process budgets are out of scope.

    Keys/values contain only immutable static grammar coordinates, never Template
    objects, interpolation values, namespaces, live session scopes or closures.
    """

    def __init__(self, *, entries: int = 32, budget: int = 32768) -> None:
        if entries <= 0 or budget <= 0:
            raise ValueError("cache bounds must be positive")
        self.entries = entries
        self.budget = budget
        self.used = 0
        self._items: OrderedDict[tuple[str, ...], tuple[tuple[PositionedToken, ...], int]] = (
            OrderedDict()
        )

    def get(
        self,
        strings: tuple[str, ...],
        build: Callable[[tuple[str, ...]], tuple[PositionedToken, ...]] = positioned_tokens,
    ) -> tuple[PositionedToken, ...]:
        existing = self._items.get(strings)

        if existing is not None:
            self._items.move_to_end(strings)

            return existing[0]
        parsed = build(strings)
        cost = sum(len(fragment.encode("utf-8")) for fragment in strings) + len(parsed) * 96

        if cost > self.budget:
            return parsed

        while self._items and (len(self._items) >= self.entries or self.used + cost > self.budget):
            _, (_, removed_cost) = self._items.popitem(last=False)
            self.used -= removed_cost
        self._items[strings] = (parsed, cost)
        self.used += cost

        return parsed

    @property
    def size(self) -> int:
        return len(self._items)
