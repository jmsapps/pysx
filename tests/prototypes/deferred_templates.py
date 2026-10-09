"""Executable deferred-scope grammar sketch, isolated from the shipped parser.

Bindings are explicit Python objects supplied in a namespace. This restricted
grammar proves for/index/destructure/nesting, let, match/case and attribute holes;
The complete positioned grammar and production resource lifecycle are out of scope.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from string.templatelib import Interpolation, Template, convert
from typing import TYPE_CHECKING, cast

from .source_maps import TemplateCoordinate

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


@dataclass(frozen=True, eq=False)
class Binding[T]:
    name: str


@dataclass(frozen=True)
class Scope:
    values: tuple[tuple[object, object], ...] = ()
    parent: Scope | None = None

    def get[T](self, binding: Binding[T]) -> T:
        for declaration, value in self.values:
            if declaration is binding:
                return cast("T", value)

        if self.parent is not None:
            return self.parent.get(binding)

        raise KeyError(f"unbound developer declaration {binding.name!r}")

    def child(self, bindings: tuple[object, ...], values: tuple[object, ...]) -> Scope:
        if len(bindings) != len(values):
            raise ValueError("destructured binding arity mismatch")

        if not all(isinstance(binding, Binding) for binding in bindings):
            raise TypeError("scope slots require explicit Binding identity")

        return Scope(tuple(zip(bindings, values, strict=True)), self)


@dataclass(frozen=True)
class Deferred[T]:
    evaluate: Callable[[Scope], T]
    names: tuple[str, ...]


def defer[A, T](binding: Binding[A], expression: Callable[[A], T]) -> Deferred[T]:
    return Deferred(lambda scope: expression(scope.get(binding)), (binding.name,))


def defer2[A, B, T](
    first: Binding[A], second: Binding[B], expression: Callable[[A, B], T]
) -> Deferred[T]:
    return Deferred(
        lambda scope: expression(scope.get(first), scope.get(second)), (first.name, second.name)
    )


@dataclass(frozen=True)
class Live[T]:
    """Stand-in live value; production reactive protocols are out of scope here."""

    read: Callable[[], T]


@dataclass(frozen=True)
class HoleRef:
    index: int


type Piece = str | HoleRef


@dataclass(frozen=True)
class NodeSpan:
    start: TemplateCoordinate
    end: TemplateCoordinate


@dataclass(frozen=True)
class Element:
    tag: str
    attributes: tuple[tuple[str, HoleRef], ...]
    content: tuple[Piece, ...]
    children: tuple[Node, ...]
    span: NodeSpan


@dataclass(frozen=True)
class Loop:
    names: tuple[str, ...]
    source: HoleRef
    children: tuple[Node, ...]
    span: NodeSpan


@dataclass(frozen=True)
class Let:
    name: str
    value: HoleRef
    span: NodeSpan


@dataclass(frozen=True)
class Match:
    value: HoleRef
    cases: tuple[tuple[str, tuple[Node, ...]], ...]
    span: NodeSpan


type Node = Element | Loop | Let | Match


def _lines(strings: tuple[str, ...]) -> list[tuple[int, tuple[Piece, ...], NodeSpan]]:
    lines: list[list[Piece]] = [[]]
    starts = [TemplateCoordinate(0, 0)]
    ends = [TemplateCoordinate(0, 0)]

    for index, fragment in enumerate(strings):
        parts = fragment.split("\n")
        lines[-1].append(parts[0])
        ends[-1] = TemplateCoordinate(index, len(parts[0]))
        offset = len(parts[0])

        for part in parts[1:]:
            offset += 1
            lines.append([part])
            starts.append(TemplateCoordinate(index, offset))
            offset += len(part)
            ends.append(TemplateCoordinate(index, offset))

        if index < len(strings) - 1:
            lines[-1].append(HoleRef(index))
    result: list[tuple[int, tuple[Piece, ...], NodeSpan]] = []

    for line, start, end in zip(lines, starts, ends, strict=True):
        if all(isinstance(piece, str) and not piece.strip() for piece in line):
            continue
        head = line[0]

        if not isinstance(head, str):
            raise ValueError("line must have a static grammar prefix")
        indent = len(head) - len(head.lstrip(" "))

        if head[:indent].find("\t") >= 0 or head.startswith("\t"):
            raise ValueError("prototype control grammar requires spaces")
        result.append(
            (
                indent,
                (head[indent:], *line[1:]),
                NodeSpan(TemplateCoordinate(start.fragment, start.offset + indent), end),
            )
        )

    return result


def parse(strings: tuple[str, ...], namespace: Mapping[str, object]) -> tuple[Node, ...]:
    """Parse structure only: interpolation values never enter the returned tree."""
    lines = _lines(strings)

    def declared(name: str) -> None:
        declaration = namespace.get(name)

        if not isinstance(declaration, Binding) or declaration.name != name:
            raise ValueError(f"{name!r} must have an explicit Binding declaration")

    def block(start: int, indent: int) -> tuple[tuple[Node, ...], int]:
        result: list[Node] = []
        cursor = start

        while cursor < len(lines) and lines[cursor][0] == indent:
            _, pieces, header_span = lines[cursor]
            prefix = cast("str", pieces[0])
            holes = tuple(piece for piece in pieces if isinstance(piece, HoleRef))
            static = "".join(piece for piece in pieces if isinstance(piece, str)).strip()
            children: tuple[Node, ...] = ()
            after = cursor + 1
            loop_match = re.fullmatch(r"for ([\w(), ]+) in\s*", static)

            if loop_match:
                if len(holes) != 1:
                    raise ValueError("for source must be one hole")
                names = tuple(re.findall(r"\b[A-Za-z_]\w*\b", loop_match.group(1)))

                for name in names:
                    declared(name)

                if after >= len(lines) or lines[after][0] <= indent:
                    raise ValueError("for requires an indented body")
                children, after = block(after, lines[after][0])
                result.append(
                    Loop(
                        names,
                        holes[0],
                        children,
                        NodeSpan(header_span.start, children[-1].span.end),
                    )
                )
            elif static.startswith("let "):
                let_match = re.fullmatch(r"let (\w+) =\s*", static)

                if let_match is None or len(holes) != 1:
                    raise ValueError("let requires a declared name and one hole")
                name = let_match.group(1)
                declared(name)
                result.append(Let(name, holes[0], header_span))
            elif static == "match":
                if len(holes) != 1:
                    raise ValueError("match requires one discriminator")
                cases: list[tuple[str, tuple[Node, ...]]] = []

                while after < len(lines) and lines[after][0] > indent:
                    case_indent, case_pieces, _ = lines[after]
                    case_header = "".join(
                        piece for piece in case_pieces if isinstance(piece, str)
                    ).strip()

                    if not case_header.startswith("case "):
                        raise ValueError("match body requires case literals")
                    key = case_header[5:].strip().strip("\"'")
                    body_at = after + 1

                    if body_at >= len(lines) or lines[body_at][0] <= case_indent:
                        raise ValueError("case requires an indented body")
                    case_nodes, after = block(body_at, lines[body_at][0])
                    cases.append((key, case_nodes))
                result.append(
                    Match(
                        holes[0],
                        tuple(cases),
                        NodeSpan(header_span.start, cases[-1][1][-1].span.end),
                    )
                )
            else:
                tag_match = re.match(r"([A-Za-z][\w-]*)(.*)", prefix)

                if tag_match is None:
                    raise ValueError("element requires a static tag")
                attrs: list[tuple[str, HoleRef]] = []
                content: list[Piece] = []
                pending = tag_match.group(2)

                for piece in pieces[1:]:
                    if isinstance(piece, str):
                        pending += piece

                        continue
                    attr_match = re.fullmatch(r"\s*(\w+)\s*=\s*", pending)

                    if attr_match:
                        attrs.append((attr_match.group(1), piece))
                    else:
                        if pending.strip():
                            content.append(pending.strip().strip("\"'"))
                        content.append(piece)
                    pending = ""

                if pending.strip():
                    content.append(pending.strip().strip("\"'"))

                if after < len(lines) and lines[after][0] > indent:
                    children, after = block(after, lines[after][0])
                result.append(
                    Element(
                        tag_match.group(1),
                        tuple(attrs),
                        tuple(content),
                        children,
                        NodeSpan(
                            header_span.start,
                            children[-1].span.end if children else header_span.end,
                        ),
                    )
                )
            cursor = after

        if cursor < len(lines) and lines[cursor][0] > indent:
            raise ValueError("unexpected indentation")

        return tuple(result), cursor

    if not lines:
        return ()
    nodes, consumed = block(0, lines[0][0])

    if consumed != len(lines):
        raise ValueError("inconsistent root indentation")

    return nodes


def _walk(nodes: tuple[Node, ...]) -> tuple[Node, ...]:
    result: list[Node] = []

    for node in nodes:
        result.append(node)

        if isinstance(node, (Element, Loop)):
            result.extend(_walk(node.children))
        elif isinstance(node, Match):
            for _, children in node.cases:
                result.extend(_walk(children))

    return tuple(result)


class ParsedCache:
    """Cache actual immutable syntax, keeping binding namespaces external.

    Fixed grammar revision and tab policy belong to this prototype class; a future
    configurable grammar must include those options in its key. The budget is a
    conservative logical payload bound, not a measured process-memory promise.
    """

    def __init__(self, *, entries: int = 32, budget: int = 32768) -> None:
        if entries <= 0 or budget <= 0:
            raise ValueError("cache bounds must be positive")
        self.entries = entries
        self.budget = budget
        self.used = 0
        self._items: OrderedDict[tuple[str, ...], tuple[tuple[Node, ...], int]] = OrderedDict()

    def get(self, strings: tuple[str, ...], namespace: Mapping[str, object]) -> tuple[Node, ...]:
        existing = self._items.get(strings)

        if existing is not None:
            nodes = existing[0]

            for node in _walk(nodes):
                names = (
                    node.names
                    if isinstance(node, Loop)
                    else (node.name,)
                    if isinstance(node, Let)
                    else ()
                )

                for name in names:
                    declaration = namespace.get(name)

                    if not isinstance(declaration, Binding) or declaration.name != name:
                        raise ValueError(f"{name!r} must have an explicit Binding declaration")
            self._items.move_to_end(strings)

            return nodes
        nodes = parse(strings, namespace)
        cost = sum(len(fragment.encode("utf-8")) for fragment in strings) + len(_walk(nodes)) * 256

        if cost > self.budget:
            return nodes

        while self._items and (len(self._items) >= self.entries or self.used + cost > self.budget):
            _, (_, removed_cost) = self._items.popitem(last=False)
            self.used -= removed_cost
        self._items[strings] = (nodes, cost)
        self.used += cost

        return nodes

    @property
    def size(self) -> int:
        return len(self._items)


class MetadataError(ValueError):
    def __init__(self, hole_index: int, reason: str = "live") -> None:
        self.hole_index = hole_index
        super().__init__(f"hole {hole_index}: unsupported {reason} conversion/format metadata")


@dataclass(frozen=True)
class Output:
    tag: str
    attributes: tuple[tuple[str, object], ...]
    text: str
    children: tuple[Output, ...]


def _resolved(value: object, scope: Scope) -> tuple[object, bool]:
    live = False

    for _ in range(16):
        if isinstance(value, Deferred):
            value = cast("Deferred[object]", value).evaluate(scope)
        elif isinstance(value, Live):
            live = True
            value = cast("Live[object]", value).read()
        else:
            return value, live

    raise ValueError("cyclic deferred/live resolution")


def evaluate(
    nodes: tuple[Node, ...],
    template: Template,
    namespace: Mapping[str, object],
    scope: Scope | None = None,
) -> tuple[Output, ...]:
    """Evaluate developer callables at runtime; static analysis never calls this."""
    holes = template.interpolations

    def value(hole: HoleRef, current: Scope, *, formatting: bool | None = False) -> object:
        interpolation = holes[hole.index]
        resolved, live = _resolved(interpolation.value, current)
        has_metadata = interpolation.conversion is not None or bool(interpolation.format_spec)

        if has_metadata and live:
            raise MetadataError(hole.index)

        if has_metadata and formatting is False:
            raise MetadataError(hole.index, "control-flow")

        if has_metadata and formatting is True:
            return format(convert(resolved, interpolation.conversion), interpolation.format_spec)

        return resolved

    def text(hole: HoleRef, current: Scope) -> str:
        interpolation: Interpolation[object] = holes[hole.index]
        resolved = value(hole, current, formatting=None)
        converted = convert(resolved, interpolation.conversion)

        return format(converted, interpolation.format_spec)

    def render(children: tuple[Node, ...], current: Scope) -> tuple[Output, ...]:
        result: list[Output] = []
        local = current

        for node in children:
            if isinstance(node, Let):
                local = local.child(
                    (namespace[node.name],), (value(node.value, local, formatting=True),)
                )
            elif isinstance(node, Loop):
                source = value(node.source, local)

                if not isinstance(source, (tuple, list)):
                    raise TypeError("loop source must be a bounded sequence snapshot")
                items = cast("tuple[object, ...] | list[object]", source)

                for item in items:
                    values = (
                        cast("tuple[object, ...]", item)
                        if len(node.names) > 1 and isinstance(item, tuple)
                        else (item,)
                    )
                    bindings = tuple(namespace[name] for name in node.names)
                    result.extend(render(node.children, local.child(bindings, values)))
            elif isinstance(node, Match):
                discriminator = str(value(node.value, local))
                selected = next(
                    (body for key, body in node.cases if key == discriminator),
                    next((body for key, body in node.cases if key == "_"), ()),
                )
                result.extend(render(selected, local))
            else:
                result.append(
                    Output(
                        node.tag,
                        tuple(
                            (name, value(hole, local, formatting=True))
                            for name, hole in node.attributes
                        ),
                        "".join(
                            piece if isinstance(piece, str) else text(piece, local)
                            for piece in node.content
                        ),
                        render(node.children, local),
                    )
                )

        return tuple(result)

    return render(nodes, scope or Scope())
