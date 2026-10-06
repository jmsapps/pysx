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

from .schema import is_event
from .template import dedent_fragments


class PysxSyntaxError(SyntaxError):
    pass


class HoleKind(Enum):
    TEXT = "TEXT"
    EVENT = "EVENT"
    ATTR = "ATTR"
    BIND = "BIND"
    COND = "COND"


@dataclass(frozen=True)
class Hole:
    index: int


@dataclass(frozen=True)
class Text:
    s: str


@dataclass
class Element:
    tag: str
    attrs: list[tuple[str, str | Hole]] = field(default_factory=list[tuple[str, str | Hole]])
    children: list[Node] = field(default_factory=lambda: list[Node]())
    namespace: str = "html"


@dataclass
class Conditional:
    hole: Hole
    then: list[Node] = field(default_factory=lambda: list[Node]())
    otherwise: list[Node] = field(default_factory=lambda: list[Node]())


type Node = Element | Conditional | str | Hole
type HoleTable = dict[int, tuple[int, HoleKind, str | None]]


class _Else(Enum):
    BRANCH = "else"


@dataclass
class Skeleton:
    root: list[Node]
    holes: list[tuple[int, HoleKind, str | None]]


Piece = Text | Hole
Line = list[Piece]


def _split_lines(fragments: tuple[str, ...]) -> list[Line]:
    lines: list[Line] = [[]]
    last = len(fragments) - 1

    for i, frag in enumerate(fragments):
        parts = frag.split("\n")
        lines[-1].append(Text(parts[0]))

        for part in parts[1:]:
            lines.append([Text(part)])

        if i < last:
            lines[-1].append(Hole(i))

    return lines


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
        while (c := self.peek()) is not None and isinstance(c, str) and c in " \t":
            self.advance()


def _take_name(sc: _Scan) -> str:
    out: list[str] = []

    while (c := sc.peek()) is not None and isinstance(c, str) and (c.isalnum() or c in "_-"):
        sc.advance()
        out.append(c)

    return "".join(out)


def _take_string(sc: _Scan) -> str:
    sc.advance()  # opening quote
    out: list[str] = []

    while True:
        c = sc.peek()

        if c is None:
            raise PysxSyntaxError("unterminated string literal")

        if isinstance(c, Hole):
            raise PysxSyntaxError("interpolation inside a string literal is not supported")

        if c == '"':
            sc.advance()

            return "".join(out)
        sc.advance()
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
            raise PysxSyntaxError("unclosed '(' — attribute lists must be single-line")
        name = _take_name(sc)

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
        elif v == '"':
            attrs.append((name, _take_string(sc)))
        else:
            raise PysxSyntaxError(f"expected a string or interpolation for {name!r}")
        sc.skip_ws()

        if sc.peek() == ",":
            sc.advance()


def _parse_content(sc: _Scan, holes: HoleTable) -> list[str | Hole]:
    items: list[str | Hole] = []

    while True:
        sc.skip_ws()
        c = sc.peek()

        if c is None:
            return items

        if c == ";":
            sc.advance()

            continue

        if isinstance(c, Hole):
            sc.advance()
            holes[c.index] = (c.index, HoleKind.TEXT, None)
            items.append(c)

            continue

        if c == '"':
            items.append(_take_string(sc))

            continue

        raise PysxSyntaxError(f"unexpected {c!r} in content")


def _parse_line(sc: _Scan, holes: HoleTable) -> Element | Conditional | _Else | list[str | Hole]:
    sc.skip_ws()
    c = sc.peek()

    if isinstance(c, str) and (c.isalpha() or c == "_"):
        tag = _take_name(sc)

        if tag == "if":
            sc.skip_ws()
            cond = sc.peek()

            if not isinstance(cond, Hole):
                raise PysxSyntaxError("'if' needs an interpolated condition: if {expr}:")
            sc.advance()
            holes[cond.index] = (cond.index, HoleKind.COND, None)
            sc.skip_ws()

            if sc.peek() != ":":
                raise PysxSyntaxError("expected ':' after if")
            sc.advance()

            return Conditional(cond)

        if tag == "else":
            sc.skip_ws()

            if sc.peek() != ":":
                raise PysxSyntaxError("expected ':' after else")
            sc.advance()

            return _Else.BRANCH

        sc.skip_ws()
        attrs: list[tuple[str, str | Hole]] = []
        had_parens = sc.peek() == "("

        if had_parens:
            sc.advance()
            attrs = _parse_attrs(sc, holes)
        sc.skip_ws()

        if sc.peek() != ":":
            # `input(...)` is a childless element. Without parentheses there is
            # nothing to separate a bare name from content, so require one.
            if had_parens and sc.eof():
                return Element(tag, attrs, [])

            raise PysxSyntaxError(f"expected ':' after element {tag!r}")
        sc.advance()

        return Element(tag, attrs, list(_parse_content(sc, holes)))

    return _parse_content(sc, holes)


def parse(strings: tuple[str, ...]) -> Skeleton:
    fragments = dedent_fragments(strings)
    lines = _split_lines(fragments)

    if lines and not _is_blank(lines[0]):
        raise PysxSyntaxError(
            "template must begin with a newline: text on the opening quote line "
            "has no recoverable indent"
        )

    root: list[Node] = []
    holes: HoleTable = {}
    stack: list[tuple[int, list[Node]]] = [(-1, root)]
    # `else:` binds to the most recent `if` opened at the same indent.
    open_conditionals: dict[int, Conditional] = {}

    for line in lines:
        if _is_blank(line):
            continue
        indent = _indent_of(line)

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        node = _parse_line(_Scan(line), holes)

        if isinstance(node, Element):
            parent.append(node)
            stack.append((indent, node.children))
        elif isinstance(node, Conditional):
            parent.append(node)
            open_conditionals[indent] = node
            stack.append((indent, node.then))
        elif isinstance(node, _Else):
            cond = open_conditionals.get(indent)

            if cond is None:
                raise PysxSyntaxError("'else' without a matching 'if' at the same indent")
            stack.append((indent, cond.otherwise))
        else:
            parent.extend(node)

    missing = [i for i in range(len(fragments) - 1) if i not in holes]

    if missing:
        raise PysxSyntaxError(f"interpolation(s) {missing} are not in a usable position")

    _namespaces(root)

    return Skeleton(root, [holes[i] for i in range(len(fragments) - 1)])


def _namespaces(nodes: list[Node], namespace: str = "html") -> None:
    for node in nodes:
        if isinstance(node, Element):
            tag = node.tag
            node.namespace = tag if tag in {"svg", "math"} else namespace
            child_namespace = node.namespace

            if tag == "foreignObject" and namespace == "svg":
                child_namespace = "html"
            _namespaces(node.children, child_namespace)
        elif isinstance(node, Conditional):
            _namespaces(node.then, namespace)
            _namespaces(node.otherwise, namespace)
