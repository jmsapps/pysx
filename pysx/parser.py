"""Fragments -> node tree + hole table.

Holes are never represented in-band. Fragments are split into lines of
Text/Hole pieces, and the scanner walks across piece boundaries, so a hole can
never be confused with template text no matter what the text contains.

The parser owns attribute names. `onClick=` is consumed here and must not
survive into the statics: emitting it would leave the renderer appending a
marker inside an unquoted `onclick` attribute, which silently swallows it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache

from .schema import is_event
from .template import dedent_fragments


@dataclass(frozen=True)
class Position:
    fragment: int
    offset: int
    line: int = 0
    column: int = 0


@dataclass(frozen=True)
class Span:
    start: Position
    end: Position


class PysxSyntaxError(SyntaxError):
    def __init__(self, message: str, position: Position | None = None) -> None:
        super().__init__(message)
        self.position = position

        if position is not None:
            self.lineno = position.line + 1
            self.offset = position.column + 1


class InterpolationError(ValueError):
    def __init__(self, message: str, position: Position | None = None) -> None:
        super().__init__(message)
        self.position = position


class LiteralText(str):
    """Immutable positioned text with ordinary string equality and rendering."""

    __slots__ = ("_span",)
    _span: Span

    def __new__(cls, value: str, span: Span) -> LiteralText:
        result = super().__new__(cls, value)
        object.__setattr__(result, "_span", span)

        return result

    @property
    def span(self) -> Span:
        return self._span

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("literal syntax nodes are immutable")


class HoleKind(Enum):
    TEXT = "TEXT"
    EVENT = "EVENT"
    ATTR = "ATTR"
    BIND = "BIND"
    COND = "COND"
    TAG = "TAG"
    SOURCE = "SOURCE"
    KEY = "KEY"
    LOCAL = "LOCAL"
    MATCH = "MATCH"
    CASE = "CASE"


@dataclass(frozen=True)
class Hole:
    index: int
    position: Position | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Text:
    s: str
    position: Position = Position(0, 0)


@dataclass(frozen=True)
class Element:
    tag: str | Hole
    attrs: tuple[tuple[str, str | Hole], ...] = ()
    children: tuple[Node, ...] = ()
    namespace: str = "html"
    span: Span | None = None


@dataclass(frozen=True)
class Conditional:
    hole: Hole
    then: tuple[Node, ...] = ()
    otherwise: tuple[Node, ...] = ()
    span: Span | None = None


@dataclass(frozen=True)
class Loop:
    names: tuple[str, ...]
    source: Hole
    key: Hole | None = None
    children: tuple[Node, ...] = ()
    span: Span | None = None


@dataclass(frozen=True)
class Local:
    operation: str
    name: str | None
    value: Hole
    span: Span | None = None


@dataclass(frozen=True)
class Case:
    patterns: tuple[str | int | float | bool | Hole | None, ...]
    wildcard: bool = False
    children: tuple[Node, ...] = ()
    span: Span | None = None


@dataclass(frozen=True)
class Match:
    value: Hole
    cases: tuple[Case, ...] = ()
    span: Span | None = None


type Node = Element | Conditional | Loop | Local | Match | str | Hole
type HoleTable = dict[int, tuple[int, HoleKind, str | None]]


@dataclass
class _Element:
    tag: str | Hole
    attrs: list[tuple[str, str | Hole]] = field(default_factory=list[tuple[str, str | Hole]])
    children: list[_Builder] = field(default_factory=lambda: list[_Builder]())
    span: Span | None = None
    body_eligible: bool = True
    colon_header: bool = False


@dataclass
class _Conditional:
    hole: Hole
    then: list[_Builder] = field(default_factory=lambda: list[_Builder]())
    otherwise: list[_Builder] = field(default_factory=lambda: list[_Builder]())
    span: Span | None = None


@dataclass
class _Loop:
    names: tuple[str, ...]
    source: Hole
    key: Hole | None = None
    children: list[_Builder] = field(default_factory=lambda: list[_Builder]())
    span: Span | None = None


@dataclass
class _Local:
    operation: str
    name: str | None
    value: Hole
    span: Span | None = None


@dataclass
class _Case:
    patterns: tuple[str | int | float | bool | Hole | None, ...]
    wildcard: bool = False
    children: list[_Builder] = field(default_factory=lambda: list[_Builder]())
    span: Span | None = None


@dataclass
class _Match:
    value: Hole
    children: list[_Builder] = field(default_factory=lambda: list[_Builder]())
    span: Span | None = None


@dataclass
class _Elif:
    hole: Hole


type _Builder = _Element | _Conditional | _Loop | _Local | _Match | _Case | str | Hole


@dataclass
class _Row:
    nodes: list[_Builder]


class _Else(Enum):
    BRANCH = "else"


@dataclass(frozen=True)
class Skeleton:
    root: tuple[Node, ...]
    holes: tuple[tuple[int, HoleKind, str | None], ...]
    strings: tuple[str, ...] = ()


Piece = Text | Hole
Line = list[Piece]


def _split_lines(fragments: tuple[str, ...], originals: tuple[str, ...]) -> list[Line]:
    lines: list[Line] = [[]]
    last = len(fragments) - 1

    row = 0
    column = 0

    for i, frag in enumerate(fragments):
        offset = 0
        source_parts = originals[i].split("\n")

        for number, part in enumerate(frag.split("\n")):
            original = source_parts[number]
            removed = len(original) - len(part)
            text = Text(part.rstrip("\r"), Position(i, offset + removed, row, column + removed))
            lines[-1].append(text)
            offset += len(original)
            column += len(original)

            if number < len(source_parts) - 1:
                offset += 1
                row += 1
                column = 0
                lines.append([])

        if i < last:
            lines[-1].append(Hole(i, Position(i, offset, row, column)))
            column += 1

    return lines


def _logical_lines(lines: list[Line]) -> list[Line]:
    result: list[Line] = []
    pending: Line = []
    depth = 0
    quote = ""
    escaped = False

    for line in lines:
        if (
            not quote
            and line
            and isinstance(line[0], Text)
            and "\t" in line[0].s[: len(line[0].s) - len(line[0].s.lstrip())]
        ):
            raise PysxSyntaxError("markup indentation uses spaces, not tabs", line[0].position)

        if pending:
            tail = pending[-1]
            position = tail.position or Position(0, 0)
            width = len(tail.s) if isinstance(tail, Text) else 1
            pending.append(
                Text(
                    "\n",
                    Position(
                        position.fragment,
                        position.offset + width,
                        position.line,
                        position.column + width,
                    ),
                )
            )
        pending.extend(line)

        for piece in line:
            if isinstance(piece, Hole):
                continue

            for char in piece.s:
                if escaped:
                    escaped = False
                elif char == "\\" and quote:
                    escaped = True
                elif quote:
                    if char == quote:
                        quote = ""
                elif char in {"'", '"'}:
                    quote = char
                elif char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1

        if depth <= 0 and not quote:
            result.append(pending)
            pending = []
            depth = 0

    if pending:
        result.append(pending)

    return result


def _is_blank(line: Line) -> bool:
    return all(isinstance(p, Text) and not p.s.strip() for p in line)


def _indent_of(line: Line) -> int:
    head = line[0]
    s = head.s if isinstance(head, Text) else ""

    return len(s) - len(s.lstrip())


def _is_event(name: str) -> bool:
    return is_event(name) or (len(name) > 2 and name.startswith("on") and name[2].isupper())


def attr_kind(name: str) -> HoleKind:
    if _is_event(name):
        return HoleKind.EVENT

    if name in {"bindValue", "bindChecked", "bindSelected"}:
        return HoleKind.BIND

    return HoleKind.ATTR


class _Scan:
    def __init__(self, line: Line) -> None:
        self.line = line
        self.pi = 0
        self.ci = 0
        self.statement_end: Position | None = None

    def position(self) -> Position:
        self._norm()
        index = min(self.pi, len(self.line) - 1)
        piece = self.line[index]
        position = piece.position or Position(0, 0)
        width = (
            self.ci if self.pi < len(self.line) else len(piece.s) if isinstance(piece, Text) else 1
        )
        text = piece.s[:width] if isinstance(piece, Text) else ""
        row = position.line + text.count("\n")
        column = len(text.rsplit("\n", 1)[-1]) if "\n" in text else position.column + width

        return Position(position.fragment, position.offset + width, row, column)

    def _norm(self) -> None:
        while self.pi < len(self.line):
            p = self.line[self.pi]

            if isinstance(p, Hole) or self.ci < len(p.s):
                return
            self.pi += 1
            self.ci = 0

    def eof(self) -> bool:
        self._norm()

        return self.pi >= len(self.line)

    def peek(self) -> str | Hole | None:
        if self.eof():
            return None
        p = self.line[self.pi]

        return p if isinstance(p, Hole) else p.s[self.ci]

    def advance(self) -> str | Hole:
        self._norm()
        p = self.line[self.pi]

        if isinstance(p, Hole):
            self.pi += 1
            self.ci = 0

            return p
        ch = p.s[self.ci]
        self.ci += 1

        return ch

    def skip_ws(self) -> None:
        while (c := self.peek()) is not None and isinstance(c, str) and c in " \t\r\n":
            self.advance()


def _take_name(sc: _Scan) -> str:
    out: list[str] = []

    while (c := sc.peek()) is not None and isinstance(c, str) and (c.isalnum() or c in "_-"):
        sc.advance()
        out.append(c)

    return "".join(out)


def _take_string(sc: _Scan) -> str:
    start = sc.position()
    quote = sc.advance()
    out: list[str] = []

    while True:
        c = sc.peek()

        if c is None:
            raise PysxSyntaxError("unterminated string literal")

        if isinstance(c, Hole):
            raise PysxSyntaxError("interpolation inside a string literal is not supported")

        if c == quote:
            sc.advance()

            return LiteralText("".join(out), Span(start, sc.position()))
        sc.advance()

        if c == "\\":
            escaped = sc.peek()

            if not isinstance(escaped, str):
                raise PysxSyntaxError("incomplete quoted escape")
            sc.advance()
            out.append(
                {"n": "\n", "r": "\r", "t": "\t", '"': '"', "'": "'", "\\": "\\"}.get(
                    escaped, "\\" + escaped
                )
            )

            continue
        out.append(c)


def _parse_attrs(sc: _Scan, holes: HoleTable) -> list[tuple[str, str | Hole]]:
    attrs: list[tuple[str, str | Hole]] = []

    while True:
        sc.skip_ws()
        c = sc.peek()

        if c == ")":
            sc.advance()

            return attrs

        if c is None:
            raise PysxSyntaxError("unclosed '(' in attribute list")
        start = sc.position()
        name = LiteralText(_take_name(sc), Span(start, sc.position()))

        if not name:
            raise PysxSyntaxError(f"expected an attribute name, found {c!r}")
        sc.skip_ws()

        if sc.peek() != "=":
            raise PysxSyntaxError(f"expected '=' after attribute {name!r}")
        sc.advance()
        sc.skip_ws()
        v = sc.peek()

        if isinstance(v, Hole):
            sc.advance()
            holes[v.index] = (v.index, attr_kind(name), name)
            attrs.append((name, v))
        elif v in {'"', "'"}:
            attrs.append((name, _take_string(sc)))
        else:
            raise PysxSyntaxError(f"expected a string or interpolation for {name!r}")
        sc.skip_ws()

        if sc.peek() == ",":
            sc.advance()


def _starts_element(sc: _Scan) -> bool:
    """Read-only lookahead: never stamp a hole until its role is decided."""
    c = sc.peek()

    if isinstance(c, str):
        return c.isalpha() or c == "_"

    if not isinstance(c, Hole):
        return False
    saved = sc.pi, sc.ci
    sc.advance()
    sc.skip_ws()
    explicit = sc.peek() in {"(", ":"}
    sc.pi, sc.ci = saved

    return explicit


def _parse_content(sc: _Scan, holes: HoleTable) -> list[str | Hole]:
    items: list[str | Hole] = []

    while True:
        sc.skip_ws()
        c = sc.peek()

        if c is None:
            return items

        if c == ";":
            saved = sc.pi, sc.ci

            while sc.peek() == ";":
                sc.advance()
                sc.skip_ws()

            if _starts_element(sc):
                sc.pi, sc.ci = saved

                return items

            continue

        if isinstance(c, Hole):
            sc.advance()
            holes[c.index] = (c.index, HoleKind.TEXT, None)
            items.append(c)
            sc.statement_end = sc.position()

            continue

        if c in {'"', "'"}:
            items.append(_take_string(sc))
            sc.statement_end = sc.position()

            continue

        raise PysxSyntaxError(f"unexpected {c!r} in content")


def _hole(sc: _Scan, holes: HoleTable, kind: HoleKind) -> Hole:
    sc.skip_ws()
    value = sc.peek()

    if not isinstance(value, Hole):
        raise PysxSyntaxError(f"{kind.value.lower()} requires an interpolation")
    sc.advance()
    holes[value.index] = (value.index, kind, None)

    return value


def _colon(sc: _Scan) -> None:
    sc.skip_ws()

    if sc.peek() != ":":
        raise PysxSyntaxError("expected ':' after control header")
    sc.advance()
    sc.skip_ws()

    if not sc.eof():
        raise PysxSyntaxError("control headers require an indented body")


def _parse_line(
    sc: _Scan, holes: HoleTable, *, allow_controls: bool = True
) -> _Element | _Conditional | _Loop | _Local | _Match | _Case | _Else | _Elif | list[str | Hole]:
    sc.skip_ws()
    c = sc.peek()

    if isinstance(c, Hole):
        saved = (sc.pi, sc.ci)
        sc.advance()
        sc.skip_ws()

        if sc.peek() in {"(", ":"}:
            holes[c.index] = (c.index, HoleKind.TAG, None)
            sc.statement_end = sc.position()
            attrs: list[tuple[str, str | Hole]] = []

            if sc.peek() == "(":
                sc.advance()
                attrs = _parse_attrs(sc, holes)
                sc.statement_end = sc.position()
                sc.skip_ws()

                if sc.eof() or sc.peek() == ";":
                    return _Element(c, attrs)

            if sc.peek() != ":":
                raise PysxSyntaxError("expected ':' after component reference")
            sc.advance()
            sc.statement_end = sc.position()

            return _Element(c, attrs, list(_parse_content(sc, holes)), colon_header=True)
        sc.pi, sc.ci = saved

    if isinstance(c, str) and (c.isalpha() or c == "_"):
        tag_start = sc.position()
        tag = _take_name(sc)
        sc.statement_end = sc.position()

        if not allow_controls and tag in {
            "if",
            "elif",
            "else",
            "for",
            "match",
            "case",
            "let",
            "set",
            "discard",
        }:
            raise PysxSyntaxError("control and local statements remain line-based", tag_start)

        if tag == "for":
            sc.skip_ws()
            names: list[str] = []
            destructured = sc.peek() == "("

            if destructured:
                sc.advance()

            while True:
                sc.skip_ws()
                name = _take_name(sc)

                if not name or name in names:
                    raise PysxSyntaxError("loop requires distinct binding names")
                names.append(name)
                sc.skip_ws()

                if not destructured or sc.peek() == ")":
                    if destructured:
                        sc.advance()

                    break

                if sc.peek() != ",":
                    raise PysxSyntaxError("expected ',' in destructured loop bindings")
                sc.advance()
            sc.skip_ws()

            if _take_name(sc) != "in":
                raise PysxSyntaxError("expected 'in' after loop bindings")
            source = _hole(sc, holes, HoleKind.SOURCE)
            sc.skip_ws()
            key: Hole | None = None

            if sc.peek() != ":":
                if _take_name(sc) != "key":
                    raise PysxSyntaxError("explicitly keyed loops require key={...}")
                sc.skip_ws()

                if sc.peek() != "=":
                    raise PysxSyntaxError("expected '=' after loop key")
                sc.advance()
                key = _hole(sc, holes, HoleKind.KEY)
            _colon(sc)

            return _Loop(tuple(names), source, key)

        if tag in {"let", "set", "discard"}:
            local_name = None

            if tag != "discard":
                sc.skip_ws()
                local_name = _take_name(sc)
                sc.skip_ws()

                if not local_name or sc.peek() != "=":
                    raise PysxSyntaxError("local bindings require name = {value}")
                sc.advance()
            local_value = _hole(sc, holes, HoleKind.LOCAL)
            sc.skip_ws()

            if not sc.eof():
                raise PysxSyntaxError("unexpected text after local binding")

            return _Local(tag, local_name, local_value)

        if tag == "match":
            match_value = _hole(sc, holes, HoleKind.MATCH)
            _colon(sc)

            return _Match(match_value)

        if tag == "case":
            sc.skip_ws()
            patterns: list[str | int | float | bool | Hole | None] = []
            wildcard = sc.peek() == "_"

            if wildcard:
                sc.advance()
            else:
                while True:
                    sc.skip_ws()
                    pattern_value = sc.peek()

                    if isinstance(pattern_value, Hole):
                        patterns.append(_hole(sc, holes, HoleKind.CASE))
                    elif pattern_value in {'"', "'"}:
                        patterns.append(_take_string(sc))
                    else:
                        token: list[str] = []

                        while isinstance(ch := sc.peek(), str) and (ch.isalnum() or ch in ".-+"):
                            token.append(ch)
                            sc.advance()
                        literal = "".join(token)

                        if literal in {"True", "False", "None"}:
                            patterns.append({"True": True, "False": False, "None": None}[literal])
                        else:
                            try:
                                patterns.append(float(literal) if "." in literal else int(literal))
                            except ValueError as exc:
                                raise PysxSyntaxError(
                                    "case requires literal alternatives or interpolations"
                                ) from exc
                    sc.skip_ws()

                    if sc.peek() != "|":
                        break
                    sc.advance()
            _colon(sc)

            return _Case(tuple(patterns), wildcard)

        if tag in {"if", "elif"}:
            sc.skip_ws()
            cond = sc.peek()

            if not isinstance(cond, Hole):
                raise PysxSyntaxError("'if' needs an interpolated condition: if {expr}:")
            sc.advance()
            holes[cond.index] = (cond.index, HoleKind.COND, None)
            sc.skip_ws()

            _colon(sc)

            return _Conditional(cond) if tag == "if" else _Elif(cond)

        if tag == "else":
            _colon(sc)

            return _Else.BRANCH

        sc.skip_ws()
        attrs = []
        had_parens = sc.peek() == "("

        if had_parens:
            sc.advance()
            attrs = _parse_attrs(sc, holes)
            sc.statement_end = sc.position()
        sc.skip_ws()

        if sc.peek() != ":":
            if sc.eof() or sc.peek() == ";":
                return _Element(tag, attrs, [], body_eligible=had_parens)

            raise PysxSyntaxError(f"expected ':' after element {tag!r}")
        sc.advance()
        sc.statement_end = sc.position()

        return _Element(tag, attrs, list(_parse_content(sc, holes)), colon_header=True)

    return _parse_content(sc, holes)


def _parse_row(
    sc: _Scan, holes: HoleTable
) -> _Row | _Conditional | _Loop | _Local | _Match | _Case | _Else | _Elif:
    nodes: list[_Builder] = []
    allow_controls = True

    while True:
        sc.skip_ws()

        while sc.peek() == ";":
            allow_controls = False
            sc.advance()
            sc.skip_ws()

        if sc.eof():
            break
        start = sc.position()
        sc.statement_end = None
        node = _parse_line(sc, holes, allow_controls=allow_controls)

        if isinstance(node, _Element):
            node.span = Span(start, sc.statement_end or sc.position())
            nodes.append(node)
        elif isinstance(node, list):
            nodes.extend(node)
        else:
            return node
        allow_controls = False

    if len(nodes) > 1:
        for item in nodes:
            if isinstance(item, _Element) and item.colon_header and not item.children:
                raise PysxSyntaxError(
                    "content-free colon header in a sibling row; use a bare name or ()",
                    item.span.start if item.span else None,
                )

    return _Row(nodes)


@lru_cache(maxsize=256)
def parse(strings: tuple[str, ...]) -> Skeleton:
    if sum(len(value.encode("utf-8")) for value in strings) > 1_048_576 or len(strings) > 16385:
        raise PysxSyntaxError("template exceeds 1 MiB or 16384 holes", Position(0, 0))
    _logical_lines(_split_lines(strings, strings))  # Validate original indentation before dedent.
    fragments = dedent_fragments(strings)
    lines = _logical_lines(_split_lines(fragments, strings))

    if lines and not _is_blank(lines[0]) and any("\n" in text for text in strings):
        raise PysxSyntaxError(
            "template must begin with a newline: text on the opening quote line "
            "has no recoverable indent",
            Position(0, 0),
        )

    root: list[_Builder] = []
    holes: HoleTable = {}
    stack: list[tuple[int, list[_Builder]]] = [(-1, root)]
    # `else:` binds to the most recent `if` opened at the same indent.
    open_conditionals: dict[tuple[int, int], tuple[_Conditional, _Conditional, bool]] = {}
    owners: dict[int, _Element | _Conditional | _Loop | _Match | _Case] = {}
    forbidden_body: tuple[int, str, int] | None = None

    for line in lines:
        if _is_blank(line):
            continue
        indent = _indent_of(line)

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        scanner = _Scan(line)
        scanner.skip_ws()
        start = scanner.position()

        try:
            row = _parse_row(scanner, holes)
        except PysxSyntaxError as exc:
            raise PysxSyntaxError(exc.msg, exc.position or scanner.position()) from exc

        node: _Element | _Conditional | _Loop | _Local | _Match | _Case | _Else | _Elif | _Row = row

        if isinstance(row, _Row) and len(row.nodes) == 1 and isinstance(row.nodes[0], _Element):
            node = row.nodes[0]

        if not isinstance(row, _Row) or row.nodes:
            if forbidden_body is not None and (
                indent > forbidden_body[0]
                or (indent == forbidden_body[0] and id(parent) != forbidden_body[2])
            ):
                raise PysxSyntaxError(forbidden_body[1], start)
            forbidden_body = None

            if isinstance(node, _Element) and not node.body_eligible:
                forbidden_body = (
                    indent,
                    f"bare element {node.tag!r} cannot own a body; add a colon: {node.tag}:",
                    id(parent),
                )
            elif isinstance(node, _Row) and any(isinstance(item, _Element) for item in node.nodes):
                forbidden_body = (
                    indent,
                    "a mixed/sibling row cannot own a body; put the parent on its own row with ':'",
                    id(parent),
                )

        if len(stack) > 128:
            raise PysxSyntaxError("template nesting exceeds 128 levels", start)

        if isinstance(node, (_Element, _Conditional, _Loop, _Local, _Match, _Case)):
            node.span = Span(
                node.span.start if isinstance(node, _Element) and node.span else start,
                scanner.position(),
            )

        if isinstance(node, (_Element, _Loop, _Match, _Case)):
            if isinstance(node, _Case) and not isinstance(owners.get(id(parent)), _Match):
                raise PysxSyntaxError("case must be directly inside match", start)
            parent.append(node)
            owners[id(node.children)] = node

            if not isinstance(node, _Element) or node.body_eligible:
                stack.append((indent, node.children))
        elif isinstance(node, _Conditional):
            parent.append(node)
            open_conditionals[id(parent), indent] = (node, node, False)
            owners[id(node.then)] = node
            owners[id(node.otherwise)] = node
            stack.append((indent, node.then))
        elif isinstance(node, (_Else, _Elif)):
            chain = open_conditionals.get((id(parent), indent))

            if chain is None or not parent or parent[-1] is not chain[0] or chain[2]:
                raise PysxSyntaxError(
                    "branch without a matching contiguous if at the same indent", start
                )
            first, tail, _closed = chain

            if isinstance(node, _Elif):
                branch = _Conditional(node.hole, span=Span(start, scanner.position()))
                tail.otherwise.append(branch)
                owners[id(branch.then)] = branch
                owners[id(branch.otherwise)] = branch
                open_conditionals[id(parent), indent] = (first, branch, False)
                stack.append((indent, branch.then))
            else:
                open_conditionals[id(parent), indent] = (first, tail, True)
                stack.append((indent, tail.otherwise))
        elif isinstance(node, _Local):
            parent.append(node)
        else:
            parent.extend(node.nodes)

        for _level, body in stack:
            owner = owners.get(id(body))

            if owner is not None and owner.span is not None:
                owner.span = Span(owner.span.start, scanner.position())

    missing = [i for i in range(len(fragments) - 1) if i not in holes]

    if missing:
        raise PysxSyntaxError(f"interpolation(s) {missing} are not in a usable position")

    return Skeleton(_freeze(root), tuple(holes[i] for i in range(len(fragments) - 1)), strings)


def _freeze(nodes: list[_Builder], namespace: str = "html") -> tuple[Node, ...]:
    frozen: list[Node] = []

    for node in nodes:
        if isinstance(node, _Element):
            tag = node.tag
            current = tag if isinstance(tag, str) and tag in {"svg", "math"} else namespace
            child_namespace = current

            if tag == "foreignObject" and namespace == "svg":
                child_namespace = "html"
            frozen.append(
                Element(
                    tag,
                    tuple(node.attrs),
                    _freeze(node.children, child_namespace),
                    current,
                    node.span,
                )
            )
        elif isinstance(node, _Conditional):
            frozen.append(
                Conditional(
                    node.hole,
                    _freeze(node.then, namespace),
                    _freeze(node.otherwise, namespace),
                    node.span,
                )
            )
        elif isinstance(node, _Loop):
            if not node.children:
                raise PysxSyntaxError(
                    "loop requires an indented body", node.span.start if node.span else None
                )
            frozen.append(
                Loop(
                    node.names, node.source, node.key, _freeze(node.children, namespace), node.span
                )
            )
        elif isinstance(node, _Local):
            frozen.append(Local(node.operation, node.name, node.value, node.span))
        elif isinstance(node, _Match):
            cases: list[Case] = []

            for child in node.children:
                if not isinstance(child, _Case):
                    raise PysxSyntaxError(
                        "match requires case blocks", node.span.start if node.span else None
                    )

                if cases and cases[-1].wildcard:
                    raise PysxSyntaxError(
                        "wildcard case must be last", child.span.start if child.span else None
                    )
                cases.append(
                    Case(
                        child.patterns,
                        child.wildcard,
                        _freeze(child.children, namespace),
                        child.span,
                    )
                )

            if not cases:
                raise PysxSyntaxError(
                    "match requires case blocks", node.span.start if node.span else None
                )
            frozen.append(Match(node.value, tuple(cases), node.span))
        elif isinstance(node, _Case):
            raise PysxSyntaxError(
                "case must be directly inside match", node.span.start if node.span else None
            )
        else:
            frozen.append(node)

    return tuple(frozen)
