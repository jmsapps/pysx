"""Safe original-source edits: template indentation, rename and owned code actions."""

from __future__ import annotations

import ast
import keyword
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

from pysx.compiler.compiler import component_binding_identity
from pysx.compiler.imports import WorkspaceResolver
from pysx.compiler.parser import line_starts
from pysx.compiler.scope import ScopeIndex
from pysx.compiler.source_map import Positions, SourceSpan

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .features import Context, TemplateCall

INDENT = 4


class UnsupportedEditError(ValueError):
    """Raised with a user-facing reason when an edit cannot be made safely."""


@dataclass(frozen=True)
class Edit:
    span: SourceSpan
    text: str


def _logical_starts(call: TemplateCall) -> list[tuple[int, int, int]]:
    """Each logical markup line as (fragment, offset after margin, own indent width)."""

    return [
        (position.fragment, position.offset, len(text) - len(text.lstrip(" ")))
        for position, text in line_starts(call.source.strings)
    ]


def _point(call: TemplateCall, fragment_index: int, offset: int) -> int:
    fragment = call.source.fragments[fragment_index]

    if offset < len(fragment.origins):
        origin = fragment.origins[offset]

        if origin is not None:
            return origin.start

    raise UnsupportedEditError("template indentation could not be mapped to its source")


def _depths(starts: Sequence[tuple[int, int, int]]) -> list[int]:
    """Nesting depth per logical line, using the grammar's own indentation stack."""
    stack: list[int] = [-1]
    out: list[int] = []

    for _fragment, _offset, width in starts:
        while len(stack) > 1 and width <= stack[-1]:
            stack.pop()

        if width > stack[-1]:
            stack.append(width)
        out.append(len(stack) - 2)

    return out


def format_edits(call: TemplateCall) -> list[Edit]:
    """Re-rank structural indentation to one unit per nesting level.

    Ranking the distinct indent widths is strictly increasing, so every
    comparison the indentation grammar makes is preserved and the result is
    idempotent. Only leading whitespace is rewritten, so text, quoted values,
    escapes, attribute continuations and holes survive byte for byte.
    """
    starts = _logical_starts(call)

    if not starts:
        return []
    edits: list[Edit] = []

    for (fragment_index, offset, width), depth in zip(starts, _depths(starts), strict=True):
        fragment = call.source.fragments[fragment_index]
        line_start = fragment.text.rfind("\n", 0, offset) + 1

        if line_start == 0 and fragment_index == 0:
            continue
        replacement = " " * (INDENT * depth)

        if replacement == fragment.text[offset : offset + width]:
            continue
        span = (
            fragment.span(offset, offset + width)
            if width
            else SourceSpan(
                _point(call, fragment_index, offset), _point(call, fragment_index, offset)
            )
        )
        edits.append(Edit(span, replacement))

    return edits


def apply_edits(source: str, edits: Sequence[Edit]) -> str:
    """Apply non-overlapping original-source edits from the end backwards."""
    ordered = sorted(edits, key=lambda edit: (edit.span.start, edit.span.end))

    for left, right in pairwise(ordered):
        if left.span.end > right.span.start:
            raise UnsupportedEditError("overlapping edits were produced; no change was made")
    out = source

    for edit in reversed(ordered):
        out = out[: edit.span.start] + edit.text + out[edit.span.end :]

    return out


def rename_target(context: Context) -> tuple[str, SourceSpan]:
    """The component tag a rename may own, or an explicit refusal."""

    if context.kind != "tag" or context.tag_span is None:
        raise UnsupportedEditError("Place the cursor on a component tag to rename it")

    if context.native is not None:
        raise UnsupportedEditError(f"{context.tag!r} is a native HTML tag and cannot be renamed")

    if not context.tag or not context.tag.isidentifier() or keyword.iskeyword(context.tag):
        raise UnsupportedEditError("Only ordinary Python names can be renamed from a template")

    if context.scope.owner(context.tag) is None:
        raise UnsupportedEditError(
            f"{context.tag!r} has no visible declaration; dynamic or generated "
            "bindings are not renamed"
        )

    return context.tag, context.tag_span


def check_new_name(name: str) -> None:
    if not name.isidentifier() or keyword.iskeyword(name):
        raise UnsupportedEditError(f"{name!r} is not a valid Python name")


def rename_edits(
    old: str,
    sources: Mapping[str, str],
    sites: Mapping[str, Sequence[SourceSpan]],
    new: str,
) -> dict[str, list[Edit]]:
    """One edit per site, refusing wholesale if any site is not the old name."""
    check_new_name(new)
    out: dict[str, list[Edit]] = {}

    for filename, spans in sites.items():
        source = sources.get(filename)

        if source is None:
            raise UnsupportedEditError(
                "A referenced file is no longer available; rename was not applied"
            )
        unique = sorted({(span.start, span.end) for span in spans}, reverse=True)

        for start, end in unique:
            if source[start:end] != old:
                raise UnsupportedEditError(
                    f"A reference in {Path(filename).name} no longer reads {old!r}; "
                    "rename was not applied"
                )
        out[filename] = [Edit(SourceSpan(start, end), new) for start, end in unique]

    if not out:
        raise UnsupportedEditError(f"No references to {old!r} were found; rename was not applied")

    return out


class RenameIndex:
    """Resolve original references against one immutable set of source overlays."""

    def __init__(self, source: str, filename: str, root: Path, buffers: Mapping[str, str]) -> None:
        self.source = source
        self.filename = filename
        self.positions = Positions(source)
        self.resolver = WorkspaceResolver(filename, roots=(root, root / "src"), buffers=buffers)

        try:
            self.tree = ast.parse(source, filename=filename)
        except SyntaxError as error:
            raise UnsupportedEditError(
                f"{Path(filename).name} cannot be analyzed completely; rename was not applied"
            ) from error
        self.scopes = ScopeIndex(self.tree, package=self.resolver.package)

    def alias_end(self, alias: ast.alias) -> int:
        if alias.end_lineno is None or alias.end_col_offset is None:
            raise UnsupportedEditError("An imported alias could not be mapped safely")

        return self.positions.ast_offset(alias.end_lineno, alias.end_col_offset)

    def reference_identity(self, span: SourceSpan) -> str | None:
        """Prove a differing backend reference is a genuine alias to the target."""

        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name) and self.positions.ast_span(node) == span:
                return component_binding_identity(
                    node.id, self.scopes.nodes[node], self.resolver, self.filename
                )

            if isinstance(node, ast.alias) and node.asname is not None:
                end = self.alias_end(node)

                if span == SourceSpan(end - len(node.asname), end):
                    return component_binding_identity(
                        node.asname, self.scopes.nodes[node], self.resolver, self.filename
                    )

        return None


def import_sites(index: RenameIndex, old: str, symbol: str) -> list[SourceSpan]:
    """Repair omitted imports only after resolving their canonical declaration.

    A genuine alias retains its local spelling while its imported source name
    follows the declaration. Explicit same-name re-exports rename both spellings.
    """
    out: list[SourceSpan] = []

    for node in ast.walk(index.tree):
        if not isinstance(node, ast.ImportFrom):
            continue

        for alias in node.names:
            if alias.name != old:
                continue
            scope = index.scopes.nodes[node]
            # Resolve this exact import independently of other writes in its
            # lexical scope, then refuse competing writes to a matching import.
            imported_node = ast.ImportFrom(module=node.module, names=[alias], level=node.level)
            isolated = ScopeIndex(
                ast.Module(body=[imported_node], type_ignores=[]), package=scope.package
            ).nodes[imported_node]
            local = alias.asname or alias.name
            imported = component_binding_identity(local, isolated, index.resolver, index.filename)

            if imported != symbol:
                continue

            if scope.binding(local) != isolated.binding(local):
                raise UnsupportedEditError(
                    f"The imported binding {local!r} has competing writes; rename was not applied"
                )
            start = index.positions.ast_offset(alias.lineno, alias.col_offset)

            if index.source[start : start + len(old)] != old:
                raise UnsupportedEditError("An imported source name could not be mapped safely")
            out.append(SourceSpan(start, start + len(old)))

            if alias.asname == old:
                end = index.alias_end(alias)
                out.append(SourceSpan(end - len(old), end))

    return out
