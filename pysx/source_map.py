"""Raw Python, decoded templates and generated Python coordinate maps.

Offsets are Unicode code points. AST columns are UTF-8 bytes; editor columns are
UTF-16 units. Disjoint assembled origins are retained, never invented as one edit.
"""

from __future__ import annotations

import ast
import bisect
import io
import re
import tokenize
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .parser import Position


@dataclass(frozen=True)
class SourceSpan:
    start: int
    end: int


@dataclass(frozen=True)
class EditorPosition:
    line: int
    character: int


@dataclass(frozen=True)
class Positions:
    source: str
    starts: tuple[int, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "starts", (0, *(m.end() for m in re.finditer("\n", self.source))))

    def text_offset(self, line: int, column: int) -> int:
        return self.starts[line - 1] + column

    def ast_offset(self, line: int, column: int) -> int:
        start = self.starts[line - 1]
        end = self.starts[line] - 1 if line < len(self.starts) else len(self.source)
        text = self.source[start:end]

        return start + len(text.encode("utf-8")[:column].decode("utf-8"))

    def ast_span(self, node: ast.expr | ast.stmt) -> SourceSpan:
        if node.end_lineno is None or node.end_col_offset is None:
            raise ValueError("AST has no end coordinates")

        return SourceSpan(
            self.ast_offset(node.lineno, node.col_offset),
            self.ast_offset(node.end_lineno, node.end_col_offset),
        )

    def editor_position(self, offset: int) -> EditorPosition:
        if not 0 <= offset <= len(self.source):
            raise ValueError("offset outside source")
        row = bisect.bisect_right(self.starts, offset) - 1
        prefix = self.source[self.starts[row] : offset]

        return EditorPosition(row, len(prefix.encode("utf-16-le")) // 2)

    def editor_offset(self, position: EditorPosition) -> int:
        if not 0 <= position.line < len(self.starts):
            raise ValueError("line outside source")
        start = self.starts[position.line]
        end = (
            self.starts[position.line + 1] - 1
            if position.line + 1 < len(self.starts)
            else len(self.source)
        )
        units = 0

        for offset in range(start, end):
            if units == position.character:
                return offset
            units += len(self.source[offset].encode("utf-16-le")) // 2

            if units > position.character:
                raise ValueError("UTF-16 coordinate splits a surrogate pair")

        if units == position.character:
            return end

        raise ValueError("column outside source")


@dataclass(frozen=True)
class MappedText:
    text: str
    origins: tuple[SourceSpan | None, ...]

    def spans(self, start: int, end: int) -> tuple[SourceSpan, ...]:
        if not 0 <= start < end <= len(self.text):
            raise ValueError("expected a nonempty mapped range")
        result: list[SourceSpan] = []

        for span in self.origins[start:end]:
            if span is None:
                raise ValueError("range includes generated scaffolding")

            if result and result[-1].start <= span.start <= result[-1].end:
                result[-1] = SourceSpan(result[-1].start, max(result[-1].end, span.end))
            else:
                result.append(span)

        return tuple(result)

    def span(self, start: int, end: int) -> SourceSpan:
        spans = self.spans(start, end)

        if len(spans) != 1:
            raise ValueError("range has disjoint origins")

        return spans[0]


_ESCAPE = re.compile(
    r"\\(?:N\{[^}]+\}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2}|[0-7]{1,3}|\r\n|\n|.)",
    re.DOTALL,
)


def _decode(raw: str, origin: int, *, raw_literal: bool) -> MappedText:
    text: list[str] = []
    origins: list[SourceSpan] = []
    cursor = 0

    while cursor < len(raw):
        width = 1
        character = raw[cursor]

        if raw.startswith("\r\n", cursor):
            character, width = "\n", 2
        elif raw.startswith(("{{", "}}"), cursor):
            width = 2
        elif character == "\\" and not raw_literal:
            match = _ESCAPE.match(raw, cursor)

            if match is None:
                raise ValueError("incomplete static escape")
            token = match.group()
            width = len(token)
            value = ast.literal_eval('"' + token + '"')

            if not isinstance(value, str):
                raise TypeError("expected static text")
            character = value
        text.extend(character)
        origins.extend(SourceSpan(origin + cursor, origin + cursor + width) for _ in character)
        cursor += width

    return MappedText("".join(text), tuple(origins))


@dataclass(frozen=True)
class TemplateSource:
    span: SourceSpan
    fragments: tuple[MappedText, ...]
    holes: tuple[ast.Interpolation, ...]
    hole_spans: tuple[SourceSpan, ...]

    @property
    def strings(self) -> tuple[str, ...]:
        return tuple(fragment.text for fragment in self.fragments)

    def location(self, position: Position, width: int = 1) -> SourceSpan:
        fragment = self.fragments[position.fragment]

        if position.offset < len(fragment.text):
            return fragment.span(position.offset, min(position.offset + width, len(fragment.text)))

        if position.fragment < len(self.holes):
            return self.hole_spans[position.fragment]

        return SourceSpan(max(self.span.start, self.span.end - 1), self.span.end)


class LiteralMapper:
    """One tokenization per source buffer; nested literals retain their own windows."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.positions = Positions(source)
        stack: list[tuple[int, bool]] = []
        windows: list[tuple[SourceSpan, bool]] = []

        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.TSTRING_START:
                stack.append((self.positions.text_offset(*token.end), "r" in token.string.lower()))
            elif token.type == tokenize.TSTRING_END:
                start, raw = stack.pop()
                windows.append((SourceSpan(start, self.positions.text_offset(*token.start)), raw))
        self.windows = tuple(sorted(windows, key=lambda item: item[0].start))

    def template(self, node: ast.TemplateStr) -> TemplateSource:
        outer = self.positions.ast_span(node)
        fragments: list[MappedText] = []
        holes: list[ast.Interpolation] = []
        text = ""
        origins: tuple[SourceSpan | None, ...] = ()

        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                span = self.positions.ast_span(value)
                parts: list[MappedText] = []

                for window, raw in self.windows:
                    if window.start < outer.start or window.end > outer.end:
                        continue
                    start, end = max(span.start, window.start), min(span.end, window.end)

                    if start < end:
                        parts.append(_decode(self.source[start:end], start, raw_literal=raw))
                decoded = "".join(part.text for part in parts)

                if decoded != value.value:
                    raise ValueError("literal source cannot be mapped safely")
                text += decoded
                origins += tuple(origin for part in parts for origin in part.origins)
            elif isinstance(value, ast.Interpolation):
                fragments.append(MappedText(text, origins))
                text, origins = "", ()
                holes.append(value)
            else:
                raise ValueError("unsupported template constant")
        fragments.append(MappedText(text, origins))

        return TemplateSource(
            outer,
            tuple(fragments),
            tuple(holes),
            tuple(self.positions.ast_span(hole) for hole in holes),
        )


def compose(left: TemplateSource, right: TemplateSource) -> TemplateSource:
    merged = MappedText(
        left.fragments[-1].text + right.fragments[0].text,
        left.fragments[-1].origins + right.fragments[0].origins,
    )

    return TemplateSource(
        SourceSpan(min(left.span.start, right.span.start), max(left.span.end, right.span.end)),
        (*left.fragments[:-1], merged, *right.fragments[1:]),
        (*left.holes, *right.holes),
        (*left.hole_spans, *right.hole_spans),
    )
