"""Ruff owns original formatting and projected lexical usage, with mapped import fixes."""

from __future__ import annotations

import ast
import json
import subprocess
from bisect import bisect_right
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict, cast

from pysx.compiler.compiler import analyze
from pysx.compiler.source_map import MappedText, Positions, SourceSpan
from pysx.tooling.tools import resolve_ruff

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from pysx.compiler.compiler import Compilation

LEXICAL_CODES = frozenset({"F401", "F811", "F821", "F823", "F841"})
COMPONENT_RETURNS = frozenset({"Fragment", "Template"})
CASING_CODES = frozenset({"N802", "N806"})


class Location(TypedDict):
    row: int
    column: int


class RuffEdit(TypedDict):
    location: Location
    end_location: Location
    content: str


class RuffFix(TypedDict):
    applicability: str
    edits: list[RuffEdit]


class RuffMessage(TypedDict):
    code: str
    message: str
    location: Location
    end_location: Location
    fix: RuffFix | None


@dataclass(frozen=True)
class Edit:
    span: SourceSpan
    text: str


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    span: SourceSpan
    edits: tuple[Edit, ...] = ()


def _ruff(source: str, filename: str, interpreter: Path | None) -> list[RuffMessage]:
    tool = resolve_ruff(interpreter)
    result = subprocess.run(
        [
            str(tool.path),
            "check",
            "--output-format=json",
            "--stdin-filename",
            filename,
            "-",
        ],
        input=source,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    if result.returncode not in {0, 1}:
        raise RuntimeError(result.stderr.strip() or "Ruff analysis failed")
    payload: object = json.loads(result.stdout)

    if not isinstance(payload, list):
        raise ValueError("Ruff did not return diagnostics")

    return cast("list[RuffMessage]", payload)


def _component_lines(tree: ast.AST) -> frozenset[int]:
    """Lines defining a component, whose PascalCase names ruff must not report."""

    lines: set[int] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        returns = node.returns
        name = None

        if isinstance(returns, ast.Name):
            name = returns.id
        elif isinstance(returns, ast.Attribute):
            name = returns.attr
        elif isinstance(returns, ast.Constant) and isinstance(returns.value, str):
            name = returns.value.strip().strip("\"'")

        if name in COMPONENT_RETURNS:
            lines.add(node.lineno)

    return frozenset(lines)


def _line_at(positions: Positions, offset: int) -> int:
    return bisect_right(positions.starts, offset)


def _span(positions: Positions, start: Location, end: Location) -> SourceSpan:
    return SourceSpan(
        positions.text_offset(start["row"], start["column"] - 1),
        positions.text_offset(end["row"], end["column"] - 1),
    )


def _import_ranges(source: str) -> tuple[SourceSpan, ...]:
    positions = Positions(source)
    result: list[SourceSpan] = []

    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        span = positions.ast_span(node)
        start = source.rfind("\n", 0, span.start) + 1

        if source[start : span.start].strip():
            start = span.start
        end = source.find("\n", span.end)

        if end == -1:
            end = len(source)
        tail = source[span.end : end].strip()
        end = min(end + 1, len(source)) if not tail or tail.startswith("#") else span.end
        result.append(SourceSpan(start, end))

    return tuple(result)


def _projected(message: RuffMessage) -> bool:
    return message["code"] in LEXICAL_CODES or message["code"].startswith("TC")


def lint_source(
    source: str,
    filename: str,
    *,
    roots: tuple[Path, ...] = (),
    buffers: Mapping[str, str] | None = None,
    compilation: Compilation | None = None,
    projection: MappedText | None = None,
    interpreter: Path | None = None,
) -> tuple[Finding, ...]:
    """Ruff comes from `interpreter`'s environment, which the editor client selects."""
    compilation = compilation or analyze(source, filename, workspace_roots=roots, buffers=buffers)

    if compilation.source != source or compilation.filename != filename:
        raise ValueError("lint compilation does not match source")
    original = MappedText(source, tuple(SourceSpan(i, i + 1) for i in range(len(source))))

    if compilation.tree is None:
        return tuple(
            Finding(
                item["code"],
                item["message"],
                _span(Positions(source), item["location"], item["end_location"]),
            )
            for item in _ruff(source, filename, interpreter)
        )
    projection = projection if projection is not None else compilation.projection()
    imports = _import_ranges(source)
    components = _component_lines(compilation.tree)
    tags = {reference.name for reference in compilation.references}
    out: dict[tuple[str, SourceSpan], Finding] = {}
    original_messages = _ruff(source, filename, interpreter)
    projected_messages = _ruff(projection.text, filename, interpreter)
    safe_imports = compilation.complete and not any(
        item["code"] in {"F821", "F823"} for item in projected_messages
    )

    for mapped, messages in (
        (original, original_messages),
        (projection, projected_messages),
    ):
        positions = Positions(mapped.text)

        for item in messages:
            if _projected(item) != (mapped is projection):
                continue
            generated = _span(positions, item["location"], item["end_location"])

            try:
                span = mapped.span(generated.start, generated.end)
            except ValueError:
                continue

            if item["code"] in CASING_CODES and (
                mapped.text[generated.start : generated.end] in tags
                or (item["code"] == "N802" and _line_at(positions, generated.start) in components)
            ):
                continue
            edits: list[Edit] = []
            fix = item["fix"]

            if (
                safe_imports
                and item["code"] == "F401"
                and fix is not None
                and fix["applicability"] == "safe"
            ):
                for edit in fix["edits"]:
                    interval = _span(positions, edit["location"], edit["end_location"])

                    try:
                        target = mapped.span(interval.start, interval.end)
                    except ValueError:
                        edits.clear()

                        break

                    if not any(
                        parent.start <= target.start < target.end <= parent.end
                        for parent in imports
                    ):
                        edits.clear()

                        break
                    edits.append(Edit(target, edit["content"]))
            out[item["code"], span] = Finding(item["code"], item["message"], span, tuple(edits))

    return tuple(out.values())
