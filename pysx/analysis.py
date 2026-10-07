"""Version-neutral JSON projection model for current buffers and editor backends."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

from .check import Diagnostic, diagnostics_for_source
from .compiler import Compilation, analyze, native_tag_for
from .parser import LiteralText
from .schema import NATIVE_TAGS, python_attr, tag_info
from .source_map import MappedText, Positions, SourceSpan

if TYPE_CHECKING:
    from collections.abc import Mapping


class Site(TypedDict):
    start: int
    end: int
    generated: int
    role: Literal["component", "prop"]
    props: NotRequired[list[str]]
    native: NotRequired[bool]


class Projection(TypedDict):
    text: str
    map: list[tuple[int, int] | None]
    sites: list[Site]
    complete: bool
    diagnostics: list[Diagnostic]
    validation: list[tuple[int, int]]


def validation_ranges(projection: MappedText) -> list[tuple[int, int]]:
    positions = Positions(projection.text)
    offsets = utf16_offsets(projection.text)
    ranges: list[tuple[int, int]] = []

    for node in ast.walk(ast.parse(projection.text)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "component_result"
        ):
            span = positions.ast_span(node)
            ranges.append((offsets[span.start], offsets[span.end]))

    return ranges


def utf16_offsets(source: str) -> tuple[int, ...]:
    offsets = [0]

    for character in source:
        offsets.append(offsets[-1] + (2 if ord(character) > 0xFFFF else 1))

    return tuple(offsets)


def editor_map(source: str, projection: MappedText) -> list[tuple[int, int] | None]:
    offsets = utf16_offsets(source)
    result: list[tuple[int, int] | None] = []

    for character, origin in zip(projection.text, projection.origins, strict=True):
        mapped = (offsets[origin.start], offsets[origin.end]) if origin is not None else None
        result.extend([mapped] * (2 if ord(character) > 0xFFFF else 1))

    return result


def sites_for(compilation: Compilation, projection: MappedText) -> list[Site]:
    targets: dict[SourceSpan, tuple[str, Literal["component", "prop"]]] = {
        reference.span: (reference.name, "component") for reference in compilation.references
    }

    for call in compilation.calls:
        for element in call.elements:
            if (
                isinstance(element.tag, str)
                and element.tag in NATIVE_TAGS
                and element.span is not None
            ):
                targets[call.source.location(element.span.start, len(element.tag))] = (
                    element.tag,
                    "component",
                )

            for name, _ in element.attrs:
                if isinstance(name, LiteralText):
                    targets[call.source.location(name.span.start, len(name))] = (name, "prop")
    source_offsets = utf16_offsets(compilation.source)
    generated_offsets = utf16_offsets(projection.text)
    positions = Positions(projection.text)
    results: dict[SourceSpan, Site] = {}

    for node in ast.walk(ast.parse(projection.text)):
        if isinstance(node, ast.Name):
            start = positions.ast_offset(node.lineno, node.col_offset)
            length = len(node.id)
            role: Literal["component", "prop"] = "component"
        elif isinstance(node, ast.Attribute):
            span = positions.ast_span(node)
            start, length = span.end - len(node.attr), len(node.attr)
            role = "component"
        elif isinstance(node, ast.keyword) and node.arg is not None:
            start = positions.ast_offset(node.lineno, node.col_offset)
            length = len(node.arg)
            role = "prop"
        else:

            continue

        try:
            origin = projection.span(start, start + length)
        except ValueError:

            continue
        target = targets.get(origin)

        if target is None or target[1] != role or origin in results:

            continue

        if isinstance(node, ast.Name) and node.id != target[0]:

            continue
        results[origin] = Site(
            start=source_offsets[origin.start],
            end=source_offsets[origin.end],
            generated=generated_offsets[start],
            role=role,
        )

        if target[0] in NATIVE_TAGS:
            results[origin]["native"] = True

    for call in compilation.calls:
        for element in call.elements:
            if not isinstance(element.tag, str):

                continue
            native_tag = native_tag_for(element.tag, call.scope, compilation.resolver)
            info = tag_info(native_tag) if native_tag is not None else None

            if info is None:

                continue
            props = sorted(
                {python_attr(name) for name in info.attributes}
                | {"ref", "css", "bind_value", "bind_checked", "bind_selected"}
            )

            for name, _ in element.attrs:
                if isinstance(name, LiteralText):
                    site = results.get(call.source.location(name.span.start, len(name)))

                    if site is not None:
                        site["props"] = props

    return list(results.values())


def projection_for_source(
    source: str,
    filename: str,
    *,
    roots: tuple[Path, ...] = (),
    buffers: Mapping[str, str] | None = None,
) -> Projection:
    compilation = analyze(source, filename, workspace_roots=roots, buffers=buffers)
    projection = compilation.projection()
    sites = sites_for(compilation, projection) if compilation.tree is not None else []

    return Projection(
        text=projection.text,
        map=editor_map(source, projection),
        sites=sites,
        complete=compilation.complete,
        diagnostics=diagnostics_for_source(
            source, filename, workspace_roots=roots, buffers=buffers
        ),
        validation=validation_ranges(projection) if compilation.tree is not None else [],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filename")
    parser.add_argument("--stdin", action="store_true")
    args = parser.parse_args()
    source = sys.stdin.read() if args.stdin else Path(args.filename).read_text("utf-8")
    print(json.dumps(projection_for_source(source, args.filename)))


if __name__ == "__main__":
    main()
