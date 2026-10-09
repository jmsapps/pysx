"""Immutable workspace projections; application modules are read, never executed."""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from .analysis import Projection, editor_map, sites_for, utf16_offsets, validation_ranges
from .check import diagnostics_for_source
from .compiler import analyze
from .compiler_imports import MAX_MODULE_BYTES
from .lint import lint_source
from .quality import read_source, source_files
from .source_map import MappedText, Positions, SourceSpan

MAX_FILES = 512
MAX_BYTES = 8 * 1024 * 1024


class Buffer(TypedDict):
    source: str
    version: int


class Request(TypedDict):
    root: str
    revision: str
    buffers: dict[str, Buffer]
    previous: NotRequired[list[Model]]


class Model(Projection):
    filename: str
    relative: str
    source: str
    version: int
    digest: str
    edits: list[tuple[int, int, str]]
    revision: str
    configuration: str
    dependencies: dict[str, str | None]


def _configuration(root: Path, files: tuple[Path, ...]) -> str:
    paths = set(Path(__file__).parent.glob("*.py"))
    directories = {root, *root.parents, *(path.parent for path in files)}

    for directory in directories:
        paths.update(
            directory / name
            for name in ("pyproject.toml", "ruff.toml", ".ruff.toml", "pyrightconfig.json")
        )
    pending = list(paths)
    seen: set[Path] = set()
    digest = hashlib.sha256(
        (
            str(root)
            + sys.executable
            + sys.version
            + importlib.metadata.version("ruff")
            + repr(files)
        ).encode()
    )

    while pending:
        path = pending.pop().resolve()

        if path in seen:
            continue
        seen.add(path)

        if path.is_file() and path.suffix == ".toml":
            settings = tomllib.loads(path.read_text())
            settings = (
                settings.get("tool", {}).get("ruff", {})
                if path.name == "pyproject.toml"
                else settings
            )
            extended = settings.get("extend")

            if isinstance(extended, str):
                target = (path.parent / extended).resolve()
                paths.add(target)
                pending.append(target)

                if len(paths) > 1024:
                    raise ValueError("editor configuration graph exceeds 1024 files")

    for path in sorted(seen):
        digest.update(str(path).encode())
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")

    return digest.hexdigest()


def _dependency_digest(path: str, buffers: dict[str, str]) -> str | None:
    source = buffers.get(path)

    if source is None:
        try:
            if Path(path).stat().st_size > MAX_MODULE_BYTES:
                return None
            source = read_source(Path(path))
        except OSError, UnicodeError:
            return None

    return hashlib.sha256(source.encode()).hexdigest()


def _rebase(model: Model, revision: str) -> Model:
    """Rebase generated namespace text only; original edit coordinates stay intact."""
    previous = model["revision"]
    offsets = utf16_offsets(model["text"])
    matches = list(re.finditer(re.escape(previous), model["text"]))
    changes = [(offsets[item.start()], offsets[item.end()]) for item in matches]
    mapping: list[tuple[int, int] | None] = []
    cursor = 0

    for start, end in changes:
        if any(value is not None for value in model["map"][start:end]):
            raise ValueError("cached revision overlaps original source")
        mapping.extend(model["map"][cursor:start])
        mapping.extend([None] * len(revision))
        cursor = end
    mapping.extend(model["map"][cursor:])
    delta = len(revision) - len(previous)

    def shifted(position: int) -> int:
        return position + sum(delta for _, end in changes if end <= position)

    result = model.copy()
    result["text"] = model["text"].replace(previous, revision)
    result["map"] = mapping
    result["revision"] = revision
    result["sites"] = []

    for original in model["sites"]:
        site = original.copy()
        site["generated"] = shifted(site["generated"])
        result["sites"].append(site)
    result["validation"] = [(shifted(start), shifted(end)) for start, end in model["validation"]]

    return result


def normalize_imports(mapped: MappedText, revision: str, modules: frozenset[str]) -> MappedText:
    """Only analysis imports change; original lexical variable names stay intact."""
    positions = Positions(mapped.text)
    patches: list[tuple[int, str, tuple[SourceSpan | None, ...]]] = []

    for node in ast.walk(ast.parse(mapped.text)):
        if (
            isinstance(node, ast.ImportFrom)
            and not node.level
            and node.module is not None
            and node.module.split(".")[0] in modules
        ):
            start = positions.ast_span(node).start + len("from ")
            prefix = revision + "."
            patches.append((start, prefix, (None,) * len(prefix)))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in modules:
                    continue
                start = positions.ast_offset(alias.lineno, alias.col_offset)
                prefix = revision + "."
                patches.append((start, prefix, (None,) * len(prefix)))

                if alias.asname is not None:
                    continue
                name = alias.name.split(".")[0]
                end = positions.ast_span(node).end
                assignment = f"; {name} = {revision}.{name}"
                origins = (
                    None,
                    None,
                    *mapped.origins[start : start + len(name)],
                    *((None,) * (len(assignment) - len(name) - 2)),
                )
                patches.append((end, assignment, origins))
    text, origins = mapped.text, mapped.origins

    for start, replacement, mapping in sorted(patches, key=lambda item: item[0], reverse=True):
        text = text[:start] + replacement + text[start:]
        origins = origins[:start] + mapping + origins[start:]

    return MappedText(text, origins)


def workspace_model(request: Request) -> list[Model]:
    root = Path(request["root"]).resolve()
    revision = request["revision"]

    if not revision.startswith("_pysx_revision_") or not revision.isidentifier():
        raise ValueError("invalid projection revision")
    roots = (root, root / "src") if (root / "src").is_dir() else (root,)
    files = source_files(root)

    if not files or len(files) > MAX_FILES:
        raise ValueError("workspace must contain between 1 and 512 Python sources")
    overlays = request["buffers"]
    buffers = {
        str(path): overlays[str(path)]["source"] if str(path) in overlays else read_source(path)
        for path in files
    }

    if sum(len(source.encode()) for source in buffers.values()) > MAX_BYTES:
        raise ValueError("workspace Python source exceeds 8 MiB")
    relative: dict[Path, Path] = {}

    for path in files:
        base = next(candidate for candidate in reversed(roots) if path.is_relative_to(candidate))
        name = path.relative_to(base)

        if name in relative.values():
            raise ValueError(f"ambiguous source roots: {name}")
        relative[path] = name
    modules = frozenset(path.parts[0].removesuffix(".py") for path in relative.values())

    if any(re.search(rf"\b{re.escape(revision)}\b", source) for source in buffers.values()):
        raise ValueError("projection revision conflicts with an original name")
    output: list[Model] = []
    configuration = _configuration(root, files)
    previous = {model["filename"]: model for model in request.get("previous", [])}

    for path in files:
        source = buffers[str(path)]
        cached = previous.get(str(path))

        if (
            cached is not None
            and cached.get("configuration") == configuration
            and cached["source"] == source
            and all(
                _dependency_digest(name, buffers) == digest
                for name, digest in cached["dependencies"].items()
            )
        ):
            model = _rebase(cached, revision)
            model["version"] = overlays[str(path)]["version"] if str(path) in overlays else -1
            output.append(model)

            continue
        compilation = analyze(source, str(path), workspace_roots=roots, buffers=buffers)
        binding = compilation.projection()
        mapped = (
            normalize_imports(binding, revision, modules)
            if compilation.tree is not None
            else binding
        )
        findings = lint_source(
            source,
            str(path),
            roots=roots,
            buffers=buffers,
            compilation=compilation,
            projection=binding,
        )
        coordinates = utf16_offsets(source)
        diagnostics = diagnostics_for_source(
            source, str(path), workspace_roots=roots, buffers=buffers, compilation=compilation
        )
        positions = Positions(source)

        for item in findings:
            if item.code not in {
                "F401",
                "F821",
                "F823",
                "F841",
                "F811",
            } and not item.code.startswith("TC"):
                continue
            start, end = (
                positions.editor_position(item.span.start),
                positions.editor_position(item.span.end),
            )
            diagnostics.append(
                {
                    "line": start.line,
                    "startChar": start.character,
                    "endLine": end.line,
                    "endChar": end.character,
                    "message": f"{item.code}: {item.message}",
                    "severity": "warning",
                }
            )
        edits = {
            (coordinates[edit.span.start], coordinates[edit.span.end], edit.text)
            for item in findings
            for edit in item.edits
        }
        output.append(
            Model(
                filename=str(path),
                relative=relative[path].as_posix(),
                source=source,
                version=overlays[str(path)]["version"] if str(path) in overlays else -1,
                digest=hashlib.sha256(source.encode()).hexdigest(),
                text=mapped.text,
                map=editor_map(source, mapped),
                sites=sites_for(compilation, mapped) if compilation.tree is not None else [],
                complete=compilation.complete,
                diagnostics=diagnostics,
                validation=validation_ranges(mapped) if compilation.tree is not None else [],
                edits=sorted(edits),
                revision=revision,
                configuration=configuration,
                dependencies={
                    str(path): digest for path, digest in compilation.resolver.dependencies.items()
                },
            )
        )

    return output


def main() -> None:
    request = cast("Request", json.loads(sys.stdin.read(MAX_BYTES * 2)))
    print(json.dumps(workspace_model(request)))


if __name__ == "__main__":
    main()
