"""Bounded source snapshots and stable, owned type-service projections."""

from __future__ import annotations

import ast
import bisect
import hashlib
import json
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from lsprotocol import types
from pygls.uris import to_fs_path

from .analysis import utf16_offsets
from .editor_config import parse_json_configuration
from .editor_model import MAX_FILES, Model, workspace_model
from .quality import read_source, source_files
from .source_map import Positions

if TYPE_CHECKING:
    from .editor_model import Buffer, Request
    from .lsp_backend import Stamp


def path_for_uri(uri: str) -> Path:
    filename = to_fs_path(uri)

    if not uri.startswith("file:") or filename is None:
        raise ValueError("Only local file URIs are supported")

    return Path(filename).resolve()


def project_settings(path: Path, seen: frozenset[Path] = frozenset()) -> dict[str, object]:
    """Flatten bounded configuration inheritance before relocating relative paths."""
    path = path.resolve()

    if path in seen or len(seen) >= 32:
        raise ValueError("Cyclic or oversized type configuration inheritance")

    if not path.is_file():
        return {}

    if path.suffix == ".toml":
        with path.open("rb") as stream:
            data = cast(
                "dict[str, object]", tomllib.load(stream).get("tool", {}).get("pyright", {})
            )
    else:
        data = parse_json_configuration(path.read_text(encoding="utf-8"))
    parent = data.pop("extends", None)
    settings = (
        project_settings(path.parent / parent, seen | {path}) if isinstance(parent, str) else {}
    )

    for key in ("venvPath", "stubPath", "typeshedPath"):
        value = data.get(key)

        if isinstance(value, str):
            data[key] = str(path.parent / value)
    paths = data.get("extraPaths")

    if isinstance(paths, list):
        data["extraPaths"] = [str(path.parent / value) for value in cast("list[str]", paths)]
    environments = data.get("executionEnvironments")

    if isinstance(environments, list):
        for environment in cast("list[dict[str, object]]", environments):
            base = environment.get("root")

            if isinstance(base, str):
                environment["root"] = str(path.parent / base)
    settings.update(data)

    return settings


def offset(source: str, position: types.Position) -> int:
    """Decode an exact UTF-16 position, refusing half-surrogates and invalid lines."""
    lines = source.splitlines(keepends=True) + ([""] if not source or source.endswith("\n") else [])

    if not 0 <= position.line < len(lines):
        raise ValueError("position line outside document")
    line = lines[position.line].rstrip("\r\n")
    units = utf16_offsets(line)
    index = bisect.bisect_left(units, position.character)

    if index == len(units) or units[index] != position.character:
        raise ValueError("position splits a Unicode character or exceeds the line")

    return sum(map(len, lines[: position.line])) + index


def position(source: str, at: int) -> types.Position:
    value = Positions(source).editor_position(at)

    return types.Position(value.line, value.character)


@dataclass(frozen=True)
class SourceSite:
    start: int
    end: int
    generated: int
    role: str
    native: bool
    props: tuple[str, ...]
    symbol: str | None = None


@dataclass(frozen=True)
class SourceFile:
    filename: str
    source: str
    text: str
    version: int
    relative: str
    mapping: tuple[tuple[int, int] | None, ...]
    complete: bool
    sites: tuple[SourceSite, ...]
    validation: tuple[tuple[int, int], ...]
    imports: tuple[tuple[int, int, str], ...] = ()

    def generated_position(self, original: types.Position) -> types.Position | None:
        at = utf16_offsets(self.source)[offset(self.source, original)]

        for site in self.sites:
            if site.start <= at < site.end:
                units = utf16_offsets(self.text)

                return position(self.text, bisect.bisect_left(units, site.generated))

        return None

    def original_range(self, target: types.Range) -> types.Range | None:
        units = utf16_offsets(self.text)
        start = units[offset(self.text, target.start)]
        end = units[offset(self.text, target.end)]
        spans = [span for span in self.mapping[start:end] if span is not None]

        if not spans:
            return None
        original = utf16_offsets(self.source)
        left, right = min(span[0] for span in spans), max(span[1] for span in spans)

        return types.Range(
            position(self.source, bisect.bisect_left(original, left)),
            position(self.source, bisect.bisect_left(original, right)),
        )

    def owns_range(self, target: types.Range) -> bool:
        units = utf16_offsets(self.text)
        start, end = units[offset(self.text, target.start)], units[offset(self.text, target.end)]

        return any(left <= start and end <= right for left, right in self.validation)


@dataclass(frozen=True)
class Snapshot:
    stamp: Stamp
    files: tuple[SourceFile, ...]
    configuration: str

    def file(self, filename: str) -> SourceFile | None:
        return next((item for item in self.files if item.filename == filename), None)


class WorkspaceIndex:
    """All mutable compiler cache data stays private; snapshots contain frozen values."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.revision = "_pysx_revision_" + uuid4().hex
        self.temporary = tempfile.TemporaryDirectory(prefix="pysx-language-")
        self.directory = Path(self.temporary.name).resolve()
        self.projected = self.directory / self.revision
        self.projected.mkdir()
        (self.projected / "__init__.py").write_text("", encoding="utf-8")
        self._models: list[Model] = []
        self._cache: dict[str, Model] = {}
        self._files: set[str] = set()
        self._buffers: dict[str, Buffer] = {}
        self.configuration = ""
        self.settings: dict[str, object] = {}

    def files(self, buffers: dict[str, Buffer]) -> set[str]:
        """Discover bounded original inputs without compiling unrelated sources."""
        additions = {Path(name).resolve() for name in buffers}

        if any(not path.is_relative_to(self.root) or path.suffix != ".py" for path in additions):
            raise ValueError("open buffers must be Python files within the workspace")
        files = set(source_files(self.root)) | additions

        if not files or len(files) > MAX_FILES:
            raise ValueError("workspace must contain between 1 and 512 Python sources")

        return set(map(str, files))

    def build(
        self, buffers: dict[str, Buffer], stamp: Stamp, *, selected: set[str] | None = None
    ) -> Snapshot:
        files = set(self.files(buffers))
        request: Request = {
            "root": str(self.root),
            "revision": self.revision,
            "buffers": buffers,
            "previous": list(self._cache.values()),
        }

        if selected is not None:
            request["selected"] = sorted(selected)
        models = workspace_model(request)
        self._files = files
        self._buffers = dict(buffers)
        self._models = models
        self._cache = {name: model for name, model in self._cache.items() if name in files}
        self._cache.update((model["filename"], model) for model in models)
        owner = self.root / "pyrightconfig.json"
        self.settings = project_settings(owner if owner.is_file() else self.root / "pyproject.toml")
        self.configuration = hashlib.sha256(
            (self._models[0]["configuration"] + json.dumps(self.settings, sort_keys=True)).encode()
        ).hexdigest()

        return Snapshot(
            stamp,
            tuple(
                SourceFile(
                    item["filename"],
                    item["source"],
                    item["text"],
                    item["version"],
                    item["relative"],
                    tuple(item["map"]),
                    item["complete"],
                    tuple(
                        SourceSite(
                            site["start"],
                            site["end"],
                            site["generated"],
                            site["role"],
                            site.get("native", False),
                            tuple(site.get("props", [])),
                            site.get("symbol"),
                        )
                        for site in item["sites"]
                    ),
                    tuple(item["validation"]),
                    tuple((start, end, text) for start, end, text in item["edits"]),
                )
                for item in self._models
            ),
            self.configuration,
        )

    def dependencies(self, filename: str) -> set[str]:
        """Find local template and ordinary import inputs, including package ancestors."""
        path = Path(filename)

        if filename not in self._files:
            return set()
        model = self._cache.get(filename)
        dependencies: set[str] = set(model["dependencies"]) if model is not None else set()
        buffered = self._buffers.get(filename)
        source = buffered["source"] if buffered is not None else read_source(path)

        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError:
            return dependencies & self._files
        roots = (self.root, self.root / "src", path.parent)

        def include(base: Path, names: tuple[str, ...]) -> None:
            for length in range(1, len(names) + 1):
                target = base.joinpath(*names[:length])
                dependencies.update((str(target.with_suffix(".py")), str(target / "__init__.py")))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "pysx":
                        continue

                    for root in roots:
                        include(root, tuple(alias.name.split(".")))
            elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
                names = tuple(node.module.split(".")) if node.module else ()

                if not node.level and names and names[0] == "pysx":
                    continue
                bases: tuple[Path, ...] = roots

                if node.level:
                    base = path.parent

                    for _ in range(node.level - 1):
                        base = base.parent
                    bases = (base,)

                for base in bases:
                    include(base, names)

                    for alias in node.names:
                        if alias.name != "*":
                            include(base, (*names, alias.name))

                    if node.level:
                        dependencies.add(str(base / "__init__.py"))

        return dependencies & self._files - {filename}

    def affected(self, previous: Snapshot | None, *, partial: bool = False) -> set[str]:
        """Compare source/dependency provenance, including unchanged consumer text."""

        if previous is None or previous.configuration != self.configuration:
            return {item["filename"] for item in self._models}
        prior = {item.filename: item for item in previous.files}
        changed = {
            item["filename"]
            for item in self._models
            if item["filename"] not in prior or prior[item["filename"]].source != item["source"]
        }
        removed: set[str] = (
            set() if partial else set(prior) - {item["filename"] for item in self._models}
        )
        dependencies = changed | removed

        return changed | {
            item["filename"]
            for item in self._models
            if dependencies.intersection(item["dependencies"])
        }

    def static_diagnostics(self, filename: str) -> list[types.Diagnostic]:
        model = self._cache.get(filename)

        if model is None:
            return []

        return [
            types.Diagnostic(
                types.Range(
                    types.Position(item["line"], item["startChar"]),
                    types.Position(item.get("endLine", item["line"]), item["endChar"]),
                ),
                item["message"],
                severity=(
                    types.DiagnosticSeverity.Error
                    if item["severity"] == "error"
                    else types.DiagnosticSeverity.Warning
                ),
                source="pysx",
                code=item["code"],
            )
            for item in model["diagnostics"]
        ]

    def write_config(self) -> bool:
        config = dict(self.settings)
        extra_paths = config.get("extraPaths", [])
        config.update(
            include=[self.revision],
            exclude=[],
            ignore=[],
            typeCheckingMode="strict",
            reportUnusedFunction="error",
            extraPaths=[str(self.root), str(self.root / "src"), *cast("list[str]", extra_paths)],
        )
        target = self.directory / "pyrightconfig.json"
        content = json.dumps(config, sort_keys=True)

        if target.is_file() and target.read_text(encoding="utf-8") == content:
            return False
        target.write_text(content, encoding="utf-8")

        return True

    def fingerprint(self) -> str:
        return hashlib.sha256("".join(item["digest"] for item in self._models).encode()).hexdigest()

    def close(self) -> None:
        self._models.clear()
        self._cache.clear()
        self._files.clear()
        self._buffers.clear()
        self.temporary.cleanup()
