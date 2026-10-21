"""Static template ownership and original-source language features."""

from __future__ import annotations

import ast
import bisect
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from lsprotocol import types
from pygls.workspace.position_codec import ServerTextPosition, ServerTextRange

from pysx.compiler.compiler import native_base_for, native_tag_for, template_source_for
from pysx.compiler.imports import WorkspaceResolver
from pysx.compiler.parser import (
    Case,
    Conditional,
    Element,
    Hole,
    LiteralText,
    Local,
    Loop,
    Match,
    Position,
    Skeleton,
    parse,
)
from pysx.compiler.scope import Scope, ScopeIndex
from pysx.compiler.source_map import LiteralMapper, MappedText, Positions, SourceSpan, utf16_offsets
from pysx.components.schema import NATIVE_TAGS, python_attr, tag_info
from pysx.runtime.forms import binding_control_error
from pysx.tooling.diagnostic_rules import component_signature
from pysx.tooling.editor.model import normalize_imports

from .workspace import SourceFile, offset, path_for_uri, position

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pygls.workspace.text_document import TextDocument

    from pysx.compiler.source_map import TemplateSource

    from .workspace import Snapshot

_MARKUP = {"pysx.pysx", "pysx.rendering.render.pysx"}
_STYLE = {
    "pysx.css",
    "pysx.global_style",
    "pysx.styling.styles.css",
    "pysx.styling.styled.global_style",
}
_STRUCTURAL = {"if", "elif", "else", "for", "let", "set", "discard", "match", "case"}


def repaired_source(source: str) -> str | None:
    """Close only lexer-proven delimiters in an analysis copy, never in the buffer."""
    brackets: list[str] = []
    quotes: list[tuple[str, int]] = []
    closing = {"(": ")", "[": "]", "{": "}"}

    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in {tokenize.TSTRING_START, tokenize.FSTRING_START}:
                match = re.search(r"['\"]", token.string)

                if match is not None:
                    quotes.append((token.string[match.start() :], len(brackets)))
            elif token.type in {tokenize.TSTRING_END, tokenize.FSTRING_END}:
                if quotes:
                    quotes.pop()
            elif token.type == tokenize.OP:
                if token.string in closing:
                    brackets.append(token.string)
                elif token.string in closing.values() and (
                    not brackets or closing[brackets.pop()] != token.string
                ):
                    return None
    except tokenize.TokenError, IndentationError:
        pass
    suffix = ""

    for quote, depth in reversed(quotes):
        while len(brackets) > depth:
            suffix += closing[brackets.pop()]
        suffix += quote

    return source + suffix + "".join(closing[value] for value in reversed(brackets))


@dataclass(frozen=True)
class Context:
    kind: str
    span: SourceSpan
    word: str
    prefix: str
    tag: str
    scope: Scope
    resolver: WorkspaceResolver
    tag_span: SourceSpan | None = None
    analysis_source: str = ""
    renderer_span: SourceSpan | None = None

    @property
    def native(self) -> str | None:
        return native_tag_for(self.tag, self.scope, self.resolver)

    @property
    def styled(self) -> bool:
        native = native_base_for(ast.Name(self.tag, ast.Load()), self.scope, self.resolver)

        if native is not None:
            return native[1]
        info = component_signature(ast.Name(self.tag, ast.Load()), self.scope, self.resolver)

        return info is not None and info[-1]


def _slot(text: str, at: int) -> tuple[str, str, int, int, int | None, int]:
    """Classify incomplete markup slots; decoded strings/holes come from the shared mapper."""
    quote: str | None = None
    escaped = False
    frames: list[tuple[str, int, int]] = []
    boundary = 0

    for index, char in enumerate(text[:at]):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None

            continue

        if char in "'\"":
            quote = char
        elif char == "(":
            match = re.search(r"([^\W\d]\w*)\s*$", text[:index])
            frames.append(
                (match[1] if match else "", index + 1, match.start(1) if match else index)
            )
        elif char == ")" and frames:
            frames.pop()
        elif char == "," and frames:
            frames[-1] = (frames[-1][0], index + 1, frames[-1][2])
        elif char in "\n:;" and not frames:
            boundary = index + 1
    word_start = at

    while word_start and (text[word_start - 1].isalnum() or text[word_start - 1] == "_"):
        word_start -= 1
    word_end = at

    while word_end < len(text) and (text[word_end].isalnum() or text[word_end] == "_"):
        word_end += 1

    if quote is not None:
        return "value", "", word_start, word_end, None, len(frames)

    if frames:
        tag, start, tag_start = frames[-1]
        raw_segment = text[start:word_start]
        segment = raw_segment.strip()
        separated = bool(raw_segment and raw_segment[-1].isspace() and not segment.endswith("="))

        return (
            ("attribute" if not segment or separated else "value"),
            tag,
            word_start,
            word_end,
            tag_start,
            len(frames),
        )

    if not text[boundary:word_start].strip():
        word = text[word_start:word_end]

        return ("text" if word in _STRUCTURAL else "tag"), word, word_start, word_end, word_start, 0

    return "text", "", word_start, word_end, None, 0


def _mapped_span(template: TemplateSource, left: int, right: int) -> SourceSpan | None:
    base = 0

    for fragment in template.fragments:
        if base <= left < right <= base + len(fragment.text):
            return fragment.span(left - base, right - base)
        base += len(fragment.text) + 1

    return None


def _literal_position(template: TemplateSource, at: int) -> tuple[str, int, int] | None:
    if any(span.start <= at < span.end for span in template.hole_spans):
        return None
    before = 0
    parts: list[str] = []
    found: tuple[int, int] | None = None

    for number, fragment in enumerate(template.fragments):
        parts.append(fragment.text)

        for index, span in enumerate(fragment.origins):
            if span is None:
                continue

            if at == span.start:
                found = (number, before + index)

                break

            if at == span.end:
                found = (number, before + index + 1)
        before += len(fragment.text)

        if number < len(template.holes):
            parts.append("\ufffc")
            before += 1

    if found is None and len(template.fragments) == 1 and not template.fragments[0].text:
        found = (0, 0)

    if found is None:
        return None

    return "".join(parts), found[1], found[0]


def classify(
    source: str, at: int, filename: str, root: Path, buffers: dict[str, str]
) -> Context | None:
    try:
        repaired = source

        try:
            tree = ast.parse(repaired)
        except SyntaxError:
            alternative = repaired_source(source)

            if alternative is None:
                return None
            repaired = alternative
            tree = ast.parse(repaired)
        mapper = LiteralMapper(repaired)
        resolver = WorkspaceResolver(filename, roots=(root, root / "src"), buffers=buffers)
        scopes = ScopeIndex(tree, package=resolver.package)
        candidates: list[tuple[int, Context]] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            scope = scopes.nodes[node]
            markup = _markup_kind(node, scope, resolver)

            if markup is None:
                continue
            style = markup[1]

            try:
                template = template_source_for(node.args[0], scope, mapper)
            except ValueError:
                if (
                    style
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    span = mapper.positions.ast_span(node.args[0])

                    if span.start < at < span.end:
                        return Context("style", SourceSpan(at, at), "", "", "", scope, resolver)

                continue

            if not template.span.start < at < template.span.end:
                continue

            if not any(window.start <= at <= window.end for window, _ in mapper.windows):
                continue
            located = _literal_position(template, at)

            if located is None:
                continue
            text, caret, fragment_index = located
            kind, tag, left, right, tag_start, _ = _slot(text, caret)
            preceding = sum(
                len(fragment.text) + 1 for fragment in template.fragments[:fragment_index]
            )
            fragment = template.fragments[fragment_index]

            if left == right:
                span = SourceSpan(at, at)
            else:
                span = fragment.span(left - preceding, right - preceding)
            candidates.append(
                (
                    template.span.end - template.span.start,
                    Context(
                        "style" if style else kind,
                        span,
                        text[left:right],
                        text[left:caret],
                        tag,
                        scope,
                        resolver,
                        _mapped_span(template, tag_start, tag_start + len(tag))
                        if tag_start is not None
                        else None,
                        repaired,
                        mapper.positions.ast_span(node),
                    ),
                )
            )
        candidates.sort(key=lambda item: item[0])

        return candidates[0][1] if candidates else None
    except SyntaxError, ValueError, tokenize.TokenError, RecursionError:
        return None


def signature_target(item: SourceFile, context: Context) -> types.Position | None:
    """Find the validation call; binding anchors are for navigation only."""

    if context.tag_span is None:
        return None
    desired = types.Range(
        position(item.source, context.tag_span.start), position(item.source, context.tag_span.end)
    )

    try:
        tree = ast.parse(item.text)
        positions = Positions(item.text)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            span = positions.ast_span(node.func)
            target = item.original_range(
                types.Range(position(item.text, span.start), position(item.text, span.end))
            )

            if target == desired:
                return position(item.text, span.end + 1)
    except SyntaxError, ValueError:
        pass

    return None


def speculative_query(
    context: Context,
    snapshot: Snapshot,
    revision: str,
) -> tuple[str, types.Position] | None:
    """Ask the type service about a lexical call in a nonexecuting analysis copy."""

    if context.renderer_span is None or not context.tag.isidentifier():
        return None
    source = context.analysis_source
    span = context.renderer_span
    prefix = f"({context.tag}(), "
    source = (
        source[: span.start] + prefix + source[span.start : span.end] + ")[1]" + source[span.end :]
    )
    at = span.start + 1 + len(context.tag) + 1

    try:
        modules = frozenset(
            file.relative.split("/", 1)[0].removesuffix(".py") for file in snapshot.files
        )
        mapped = MappedText(source, tuple(SourceSpan(i, i + 1) for i in range(len(source))))
        projection = normalize_imports(mapped, revision, modules)
        target = next(
            (
                index
                for index, origin in enumerate(projection.origins)
                if origin is not None and origin.start == at
            ),
            None,
        )

        return (projection.text, position(projection.text, target)) if target is not None else None
    except ValueError, SyntaxError, RecursionError:
        return None


def callable_parameters(context: Context) -> tuple[str, ...]:
    info = component_signature(ast.Name(context.tag, ast.Load()), context.scope, context.resolver)

    if info is None:
        return ()
    function, bound, _, _, _ = info

    regular_bound = max(0, bound - len(function.args.posonlyargs))

    return tuple(arg.arg for arg in function.args.args[regular_bound:] + function.args.kwonlyargs)


def signature(context: Context) -> types.SignatureHelp | None:
    info = component_signature(ast.Name(context.tag, ast.Load()), context.scope, context.resolver)

    if info is None:
        return None
    function, bound, fixed, _, _ = info
    arguments = function.args
    positional = arguments.posonlyargs + arguments.args
    defaults = dict(
        zip(
            (arg.arg for arg in positional[len(positional) - len(arguments.defaults) :]),
            arguments.defaults,
            strict=True,
        )
    )
    defaults.update(
        {
            arg.arg: value
            for arg, value in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True)
            if value is not None
        }
    )
    names = positional[bound:] + arguments.kwonlyargs
    labels = [
        arg.arg
        + (": " + ast.unparse(arg.annotation) if arg.annotation else "")
        + (
            " = <bound>"
            if arg.arg in fixed
            else " = " + ast.unparse(defaults[arg.arg])
            if arg.arg in defaults
            else ""
        )
        for arg in names
    ]

    remaining_posonly = max(0, len(arguments.posonlyargs) - bound)

    if remaining_posonly:
        labels.insert(remaining_posonly, "/")
    insertion = len(positional[bound:]) + bool(remaining_posonly)

    if arguments.vararg is not None:
        labels.insert(
            insertion,
            "*"
            + arguments.vararg.arg
            + (
                ": " + ast.unparse(arguments.vararg.annotation)
                if arguments.vararg.annotation
                else ""
            ),
        )
    elif arguments.kwonlyargs:
        labels.insert(insertion, "*")

    if arguments.kwarg is not None:
        labels.append(
            "**"
            + arguments.kwarg.arg
            + (": " + ast.unparse(arguments.kwarg.annotation) if arguments.kwarg.annotation else "")
        )
    result = ast.unparse(function.returns) if function.returns is not None else "unknown"
    label = "async " if isinstance(function, ast.AsyncFunctionDef) else ""
    label += f"{context.tag}({', '.join(labels)}) -> {result}"

    if fixed:
        label += " (partially bound)"

    return types.SignatureHelp(
        [
            types.SignatureInformation(
                label,
                parameters=[
                    types.ParameterInformation(value) for value in labels if value not in {"*", "/"}
                ],
            )
        ],
        active_signature=0,
    )


def completions(context: Context) -> list[types.CompletionItem]:
    names: set[str] = set()

    if context.kind == "tag":
        names.update(NATIVE_TAGS)
        scope: Scope | None = context.scope

        while scope is not None:
            for name in scope.bindings:
                if (
                    native_tag_for(name, context.scope, context.resolver) is not None
                    or component_signature(
                        ast.Name(name, ast.Load()), context.scope, context.resolver
                    )
                    is not None
                ):
                    names.add(name)
            scope = scope.parent
    elif context.kind == "attribute":
        native = context.native
        info = tag_info(native) if native is not None else None

        if info is not None:
            names.update(python_attr(name) for name in info.attributes)
            names.update({"ref", "css"})
            names.update(
                python_attr(name)
                for name in ("bindValue", "bindChecked", "bindSelected")
                if binding_control_error(name, info.name, None, None, None) is None
            )
        else:
            names.update(callable_parameters(context))

        if context.styled:
            names.add("variant")

    return [
        types.CompletionItem(
            name,
            kind=(
                types.CompletionItemKind.Property
                if context.kind == "attribute"
                else types.CompletionItemKind.Class
            ),
        )
        for name in sorted(names)
        if name.startswith(context.prefix)
    ]


def client_range(document: TextDocument, source: str, span: SourceSpan) -> types.Range:
    positions = Positions(source)
    start_line = bisect.bisect_right(positions.starts, span.start) - 1
    end_line = bisect.bisect_right(positions.starts, span.end) - 1

    return document.range_to_client_units(
        ServerTextRange(
            ServerTextPosition(start_line, span.start - positions.starts[start_line]),
            ServerTextPosition(end_line, span.end - positions.starts[end_line]),
        )
    )


def mapped_locations(
    snapshot: Snapshot,
    projected: Path,
    result: types.Location | Sequence[types.Location] | Sequence[types.LocationLink] | None,
    *,
    expand: bool,
    symbol: str | None = None,
) -> list[types.Location]:
    if result is None:
        return []
    locations: list[types.Location | types.LocationLink] = []

    if isinstance(result, types.Location):
        locations.append(result)
    else:
        locations.extend(result)
    output: dict[tuple[str, int, int, int, int], types.Location] = {}

    for location in locations:
        uri = location.uri if isinstance(location, types.Location) else location.target_uri
        target = (
            location.range
            if isinstance(location, types.Location)
            else location.target_selection_range
        )
        path = path_for_uri(uri)
        item = next((item for item in snapshot.files if projected / item.relative == path), None)

        if item is None:
            if not path.is_relative_to(projected.parent):
                output[
                    (
                        uri,
                        target.start.line,
                        target.start.character,
                        target.end.line,
                        target.end.character,
                    )
                ] = types.Location(uri, target)

            continue
        mapped = item.original_range(target)

        if mapped is None:
            continue
        ranges = [mapped]

        if expand:
            units = utf16_offsets(item.text)
            left, right = (
                units[offset(item.text, target.start)],
                units[offset(item.text, target.end)],
            )

            for site in item.sites:
                if left <= site.generated < right:
                    original_units = utf16_offsets(item.source)
                    ranges.append(
                        types.Range(
                            position(item.source, bisect.bisect_left(original_units, site.start)),
                            position(item.source, bisect.bisect_left(original_units, site.end)),
                        )
                    )
        original_uri = Path(item.filename).as_uri()

        for value in ranges:
            key = (
                original_uri,
                value.start.line,
                value.start.character,
                value.end.line,
                value.end.character,
            )
            output[key] = types.Location(original_uri, value)

    if expand and symbol is not None:
        for item in snapshot.files:
            units = utf16_offsets(item.source)

            for site in item.sites:
                if site.symbol != symbol:
                    continue
                uri = Path(item.filename).as_uri()
                target = types.Range(
                    position(item.source, bisect.bisect_left(units, site.start)),
                    position(item.source, bisect.bisect_left(units, site.end)),
                )
                output[
                    (
                        uri,
                        target.start.line,
                        target.start.character,
                        target.end.line,
                        target.end.character,
                    )
                ] = types.Location(uri, target)

    return list(output.values())


SEMANTIC_TOKEN_TYPES = ("type", "class", "property", "keyword", "variable", "string")
SEMANTIC_TOKEN_MODIFIERS = ("defaultLibrary",)

_KEYWORD = re.compile(r"(if|elif|else|for|let|set|discard|match|case)\b")
_BINDING = re.compile(r"(?:let|set)\s+([A-Za-z_]\w*)")


@dataclass(frozen=True)
class TemplateCall:
    """One markup call located in a buffer, with its parsed structure."""

    source: TemplateSource
    skeleton: Skeleton
    scope: Scope
    resolver: WorkspaceResolver
    call_span: SourceSpan


def _markup_kind(
    node: ast.Call, scope: Scope, resolver: WorkspaceResolver
) -> tuple[bool, bool] | None:
    """Return (is_markup, is_style) for calls this module owns, else None."""
    identity = resolver.identity(scope.identity(node.func))
    style = (
        identity in _STYLE
        or (identity is not None and identity.startswith(("pysx.styled.", "pysx.styling.styled.")))
        or (
            isinstance(node.func, ast.Call)
            and resolver.identity(scope.identity(node.func.func))
            in {"pysx.styled", "pysx.styling.styled.styled"}
        )
    )

    if identity not in _MARKUP and not style:
        return None

    return not style, style


def template_calls(
    source: str, filename: str, root: Path, buffers: dict[str, str]
) -> list[TemplateCall]:
    """Enumerate every parseable markup template in a buffer, ordered by position."""

    try:
        repaired = source

        try:
            tree = ast.parse(repaired)
        except SyntaxError:
            alternative = repaired_source(source)

            if alternative is None:
                return []
            repaired = alternative
            tree = ast.parse(repaired)
        mapper = LiteralMapper(repaired)
        resolver = WorkspaceResolver(filename, roots=(root, root / "src"), buffers=buffers)
        scopes = ScopeIndex(tree, package=resolver.package)
    except SyntaxError, ValueError, tokenize.TokenError, RecursionError:
        return []
    found: list[TemplateCall] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue

        try:
            scope = scopes.nodes[node]
            kind = _markup_kind(node, scope, resolver)

            if kind is None or not kind[0]:
                continue
            template = template_source_for(node.args[0], scope, mapper)
            skeleton = parse(template.strings)
        except KeyError, SyntaxError, ValueError, TypeError, RecursionError:
            continue
        found.append(
            TemplateCall(template, skeleton, scope, resolver, mapper.positions.ast_span(node))
        )

    return sorted(found, key=lambda call: call.source.span.start)


def _children(node: object) -> tuple[object, ...]:
    if isinstance(node, (Element, Loop, Case)):
        return tuple(node.children)

    if isinstance(node, Conditional):
        return (*node.then, *node.otherwise)

    if isinstance(node, Match):
        return tuple(node.cases)

    return ()


def _keyword_at(call: TemplateCall, node: object) -> tuple[str, SourceSpan] | None:
    if not isinstance(node, (Element, Conditional, Loop, Local, Match, Case)) or node.span is None:
        return None
    span = node.span
    fragment = call.source.fragments[span.start.fragment]
    match = _KEYWORD.match(fragment.text, span.start.offset)

    if match is None:
        return None

    return match.group(1), call.source.location(span.start, len(match.group(1)))


def _else_keyword(call: TemplateCall, node: Conditional) -> SourceSpan | None:
    """Locate the `else` header; elif branches carry their own node and keyword."""
    first = next(
        (
            child
            for child in node.otherwise
            if isinstance(child, (Element, Conditional, Loop, Local, Match)) and child.span
        ),
        None,
    )

    if first is None or first.span is None:
        return None
    branch = _keyword_at(call, first)

    if isinstance(first, Conditional) and branch is not None and branch[0] == "elif":
        return None
    span = first.span
    fragment = call.source.fragments[span.start.fragment].text
    index = fragment.rfind("else", 0, span.start.offset)

    if index < 0 or fragment[index + 4 : span.start.offset].strip() not in {":", ""}:
        return None

    return call.source.location(Position(span.start.fragment, index), len("else"))


def _node_span(call: TemplateCall, node: object) -> SourceSpan | None:
    span = getattr(node, "span", None)

    if span is None:
        return None

    try:
        start = call.source.location(span.start, 1)
        end = call.source.location(span.end, 1)
    except ValueError, IndexError:
        return None

    return SourceSpan(start.start, max(start.end, end.start))


def semantic_tokens(calls: Sequence[TemplateCall]) -> list[tuple[SourceSpan, str, tuple[str, ...]]]:
    """Token spans for markup the classifier owns; Python regions stay unclaimed."""
    tokens: set[tuple[SourceSpan, str, tuple[str, ...]]] = set()

    for call in calls:
        for node in _walk(call.skeleton.root):
            # Categories come from the literal grammar, so resolving one literal
            # at multiple call sites cannot create a second token at its origin.
            tokens.update(_node_tokens(call, node))

    return sorted(
        (token for token in tokens if token[0].start < token[0].end),
        key=lambda token: (token[0].start, token[0].end, token[1], token[2]),
    )


def _node_tokens(call: TemplateCall, node: object) -> list[tuple[SourceSpan, str, tuple[str, ...]]]:
    out: list[tuple[SourceSpan, str, tuple[str, ...]]] = []
    found = _keyword_at(call, node)

    if isinstance(node, Element):
        if isinstance(node.tag, str) and node.span is not None:
            native = node.tag in NATIVE_TAGS
            out.append(
                (
                    call.source.location(node.span.start, len(node.tag)),
                    "type" if native else "class",
                    ("defaultLibrary",) if native else (),
                )
            )

        for name, value in node.attrs:
            if isinstance(name, LiteralText):
                out.append((call.source.location(name.span.start, len(name)), "property", ()))

            if isinstance(value, LiteralText) and name not in {"css", "style"}:
                width = value.span.end.offset - value.span.start.offset
                out.append((call.source.location(value.span.start, width), "string", ()))

        return out

    if found is not None:
        out.append((found[1], "keyword", ()))

    if isinstance(node, Conditional) and node.otherwise:
        branch = _else_keyword(call, node)

        if branch is not None:
            out.append((branch, "keyword", ()))

    if isinstance(node, Loop) and node.span is not None:
        cursor = node.span.start.offset + len("for")
        fragment = call.source.fragments[node.span.start.fragment].text

        for name in node.names:
            index = fragment.find(name, cursor)

            if index < 0:
                break
            out.append(
                (
                    call.source.location(Position(node.span.start.fragment, index), len(name)),
                    "variable",
                    (),
                )
            )
            cursor = index + len(name)
    elif isinstance(node, Local) and node.name is not None and node.span is not None:
        fragment = call.source.fragments[node.span.start.fragment].text
        match = _BINDING.match(fragment, node.span.start.offset)

        if match is not None:
            out.append(
                (
                    call.source.location(
                        Position(node.span.start.fragment, match.start(1)), len(node.name)
                    ),
                    "variable",
                    (),
                )
            )

    return out


def _walk(nodes: Sequence[object]) -> list[object]:
    out: list[object] = []

    for node in nodes:
        if isinstance(node, (str, Hole)):
            continue
        out.append(node)
        out.extend(_walk(_children(node)))

    return out


def folding_ranges(calls: Sequence[TemplateCall], source: str) -> list[tuple[int, int]]:
    """Fold every multi-line markup construct, outermost first."""
    positions = Positions(source)
    out: list[tuple[int, int]] = []

    for call in calls:
        for node in _walk(call.skeleton.root):
            span = _node_span(call, node)

            if span is None:
                continue
            start = bisect.bisect_right(positions.starts, span.start) - 1
            end = bisect.bisect_right(positions.starts, span.end) - 1

            if end > start:
                out.append((start, end))

    return sorted(set(out))


def document_symbols(
    calls: Sequence[TemplateCall],
) -> list[tuple[str, str, SourceSpan, SourceSpan, list[object]]]:
    """Hierarchical markup symbols; the selection span always lies inside the range."""

    def build(call: TemplateCall, nodes: Sequence[object]) -> list[object]:
        out: list[object] = []

        for node in nodes:
            span = _node_span(call, node)

            if span is None:
                continue
            found = _keyword_at(call, node)

            if isinstance(node, Element) and isinstance(node.tag, str) and node.span is not None:
                name = node.tag
                selection = call.source.location(node.span.start, len(node.tag))
            elif found is not None:
                name, selection = found
            else:
                continue

            if isinstance(node, Conditional):
                # `elif` owns its own node, so it is a sibling; `else` has none.
                branch = _else_keyword(call, node)
                out.append((name, "control", span, selection, build(call, node.then)))

                if branch is None:
                    out.extend(build(call, node.otherwise))
                else:
                    out.append(
                        (
                            "else",
                            "control",
                            SourceSpan(branch.start, span.end),
                            branch,
                            build(call, node.otherwise),
                        )
                    )

                continue
            out.append(
                (
                    name,
                    "element" if isinstance(node, Element) else "control",
                    span,
                    selection,
                    build(call, _children(node)),
                )
            )

        return out

    result: list[object] = []

    for call in calls:
        result.extend(build(call, call.skeleton.root))

    return cast("list[tuple[str, str, SourceSpan, SourceSpan, list[object]]]", result)


def _descend(call: TemplateCall, nodes: Sequence[object], at: int) -> list[SourceSpan]:
    for node in nodes:
        span = _node_span(call, node)

        if span is None or not span.start <= at <= span.end:
            continue

        return [span, *_descend(call, _children(node), at)]

    return []


def selection_spans(calls: Sequence[TemplateCall], at: int) -> list[SourceSpan]:
    """Innermost-to-outermost markup spans containing an original-source offset."""

    for call in calls:
        if not call.source.span.start <= at <= call.source.span.end:
            continue
        chain = _descend(call, call.skeleton.root, at)
        chain.reverse()
        chain.append(call.source.span)
        chain.append(call.call_span)

        return chain

    return []
