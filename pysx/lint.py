"""Ruff owns original formatting and projected lexical usage, with mapped import fixes."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

from .compiler import analyze
from .source_map import MappedText, Positions, SourceSpan

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .compiler import Compilation

LEXICAL_CODES = frozenset({"F401", "F811", "F821", "F823", "F841"})


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


def _ruff(source: str, filename: str) -> list[RuffMessage]:
    binary = shutil.which("ruff", path=str(Path(sys.executable).parent))
    command = [binary] if binary is not None else [sys.executable, "-m", "ruff"]
    result = subprocess.run(
        [
            *command,
            "check",
            "--output-format=json",
            "--stdin-filename",
            filename,
            "-",
        ],
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode not in {0, 1}:

        raise RuntimeError(result.stderr.strip() or "Ruff analysis failed")
    payload: object = json.loads(result.stdout)

    if not isinstance(payload, list):

        raise ValueError("Ruff did not return diagnostics")

    return cast("list[RuffMessage]", payload)


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
) -> tuple[Finding, ...]:
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
            for item in _ruff(source, filename)
        )
    projection = projection if projection is not None else compilation.projection()
    imports = _import_ranges(source)
    out: dict[tuple[str, SourceSpan], Finding] = {}

    for mapped, messages in (
        (original, _ruff(source, filename)),
        (projection, _ruff(projection.text, filename)),
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
            edits: list[Edit] = []
            fix = item["fix"]

            if (
                compilation.complete
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
