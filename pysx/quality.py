"""Template-aware strict quality commands over every maintained Python source."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tempfile
import tokenize
import tomllib
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .compiler import analyze
from .lint import lint_source
from .source_map import EditorPosition, MappedText, Positions, SourceSpan

if TYPE_CHECKING:
    from collections.abc import Mapping

EXCLUDED = frozenset({"node_modules", "__pycache__", "build", "dist"})


def source_files(root: Path) -> tuple[Path, ...]:
    """Match the maintained tree; hidden/vendor/cache directories never become inputs."""

    return tuple(
        sorted(
            path
            for path in root.rglob("*.py")
            if not any(
                part.startswith((".", "_pysx_revision_")) or part in EXCLUDED
                for part in path.relative_to(root).parts
            )
        )
    )


def read_source(path: Path) -> str:
    with tokenize.open(path) as stream:
        return stream.read()


def _position(source: str, span: SourceSpan) -> str:
    value = Positions(source).editor_position(span.start)

    return f"{value.line + 1}:{value.character + 1}"


def lint_project(root: Path, *, fix_imports: bool = False) -> int:
    files = source_files(root)

    if not files:
        raise ValueError("no Python sources discovered")
    errors = 0
    buffers = {str(path): read_source(path) for path in files}

    for path in files:
        source = buffers[str(path)]
        findings = lint_source(source, str(path), roots=(root,), buffers=buffers)

        if fix_imports:
            edits = {edit for item in findings for edit in item.edits}
            ordered = sorted(edits, key=lambda item: item.span.start, reverse=True)

            if any(left.span.start < right.span.end for left, right in pairwise(ordered)):
                raise ValueError(f"conflicting import fixes for {path}")

            for edit in ordered:
                source = source[: edit.span.start] + edit.text + source[edit.span.end :]

            if source != buffers[str(path)]:
                if read_source(path) != buffers[str(path)]:
                    raise ValueError(f"source changed before import fixes: {path}")
                path.write_text(source, encoding="utf-8")
                findings = lint_source(
                    source, str(path), roots=(root,), buffers={**buffers, str(path): source}
                )

        for item in findings:
            print(f"{path}:{_position(source, item.span)}: {item.code} {item.message}")
            errors += 1
    print(
        f"Ruff: analyzed {len(files)} original sources and binding projections; {errors} findings"
    )

    return int(errors > 0)


def _mapped_range(
    mapped: MappedText, start: EditorPosition, end: EditorPosition
) -> SourceSpan | None:
    positions = Positions(mapped.text)
    first, last = positions.editor_offset(start), positions.editor_offset(end)

    try:
        return mapped.span(first, last)
    except ValueError:
        # Call diagnostics can cover scaffolding plus an actual tag/prop. Keep the
        # narrow mapped origin rather than exposing a private generated filename.
        origins = [span for span in mapped.origins[first:last] if span is not None]

        if origins:
            return min(origins, key=lambda span: span.end - span.start)

        return _validation_origin(mapped, first)


def _validation_origin(mapped: MappedText, offset: int) -> SourceSpan | None:
    positions = Positions(mapped.text)

    for node in ast.walk(ast.parse(mapped.text)):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != "component_result"
        ):
            continue
        span = positions.ast_span(node)

        if span.start <= offset < span.end:
            return next(
                (origin for origin in mapped.origins[span.start : span.end] if origin is not None),
                None,
            )

    return None


def type_project(root: Path, backend: str) -> int:
    files = source_files(root)

    if not files:
        raise ValueError("no Python sources discovered")
    buffers = {str(path): read_source(path) for path in files}
    with tempfile.TemporaryDirectory(prefix="pysx-quality-") as directory:
        tree = Path(directory).resolve()
        models: dict[Path, tuple[Path, str, MappedText]] = {}

        for path in files:
            compilation = analyze(
                buffers[str(path)], str(path), workspace_roots=(root,), buffers=buffers
            )
            mapped = compilation.projection()
            target = tree / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(mapped.text, encoding="utf-8")
            models[target] = (path, buffers[str(path)], mapped)
        config = root / "pyproject.toml"

        if config.is_file():
            (tree / "pyproject.toml").write_bytes(config.read_bytes())

        if backend == "pyright":
            settings: dict[str, object] = {}

            if config.is_file():
                settings = cast(
                    "dict[str, object]",
                    tomllib.loads(config.read_text()).get("tool", {}).get("pyright", {}),
                )
            (tree / "pyrightconfig.json").write_text(
                json.dumps(dict(settings, reportUnusedFunction="error")), encoding="utf-8"
            )

        for package in files:
            marker = package.parent / "py.typed"

            if marker.is_file():
                destination = tree / marker.relative_to(root)
                destination.write_bytes(marker.read_bytes())
        venv = root / ".venv"

        if venv.is_dir():
            (tree / ".venv").symlink_to(venv, target_is_directory=True)
        targets = [str(path) for path in models]
        command = (
            [sys.executable, "-m", "mypy", "--output=json", "--verbose", "--no-incremental"]
            if backend == "mypy"
            else [sys.executable, "-m", "pyright", "--outputjson"]
        )
        result = subprocess.run(
            [*command, *targets], cwd=tree, check=False, capture_output=True, text=True
        )

        if result.returncode not in {0, 1}:
            raise RuntimeError(result.stdout + "\n" + result.stderr[-1500:])
        messages: list[dict[str, object]] = []

        if backend == "mypy":
            found = set(re.findall(r"Found source:\s+BuildSource\(path='([^']+)'", result.stderr))

            if not set(targets) <= found:
                raise RuntimeError("mypy did not report all requested projection inputs")
            messages = [
                cast("dict[str, object]", json.loads(line))
                for line in result.stdout.splitlines()
                if line.startswith("{")
            ]
        else:
            payload = cast("dict[str, object]", json.loads(result.stdout))
            summary = cast("Mapping[str, int]", payload["summary"])

            if summary["filesAnalyzed"] < len(files):
                raise RuntimeError("Pyright skipped requested projection inputs")
            messages = cast("list[dict[str, object]]", payload["generalDiagnostics"])
        seen: set[tuple[Path, SourceSpan, str]] = set()
        errors = 0

        for message in messages:
            if message.get("severity") != "error":
                continue
            reported = Path(str(message["file"]))
            model = models.get((reported if reported.is_absolute() else tree / reported).resolve())

            if model is None:
                raise RuntimeError(f"unmapped analyzer error: {message}")
            path, source, mapped = model

            if backend == "mypy":
                positions = Positions(mapped.text)
                begin = positions.text_offset(
                    int(cast("int", message["line"])), int(cast("int", message.get("column", 0)))
                )
                span = (
                    mapped.origins[min(begin, max(len(mapped.origins) - 1, 0))]
                    if mapped.origins
                    else None
                )

                if span is None:
                    span = _validation_origin(mapped, begin)
            else:
                location = cast("dict[str, dict[str, int]]", message["range"])
                span = _mapped_range(
                    mapped,
                    EditorPosition(**location["start"]),
                    EditorPosition(**location["end"]),
                )

            if span is None:
                raise RuntimeError(f"analyzer error in generated scaffolding: {message['message']}")
            key = (path, span, str(message["message"]))

            if key not in seen:
                seen.add(key)
                print(f"{path}:{_position(source, span)}: {message['message']}")
                errors += 1
        print(f"{backend}: analyzed {len(files)} explicit projections; {errors} errors")

        return int(errors > 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", choices=("ruff", "mypy", "pyright"))
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--fix-imports", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()

    return (
        lint_project(root, fix_imports=args.fix_imports)
        if args.backend == "ruff"
        else type_project(root, args.backend)
    )


if __name__ == "__main__":
    raise SystemExit(main())
