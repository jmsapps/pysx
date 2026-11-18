"""Marker-based project discovery; reading configuration never executes it."""

from __future__ import annotations

import configparser
import tomllib
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pathlib import Path

MARKERS: tuple[str, ...] = ("pyproject.toml", "setup.cfg", "setup.py")
MAX_DEPTH = 64
MAX_CONFIGURATION_BYTES = 1_048_576


def project_root(path: Path, *, bound: Path | None = None) -> Path | None:
    """Find the innermost directory owning the file, never searching past `bound`."""
    directory = path.absolute()

    if directory.is_file() or directory.suffix:
        directory = directory.parent
    limit = bound.absolute() if bound is not None else None

    if limit is not None and not directory.is_relative_to(limit):
        return None

    for _ in range(MAX_DEPTH):
        if any((directory / marker).is_file() for marker in MARKERS):
            return directory

        if directory == limit or directory.parent == directory:
            return None
        directory = directory.parent

    return None


def _mapping(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]

    if isinstance(value, list):
        return [item for item in cast("list[object]", value) if isinstance(item, str)]

    if isinstance(value, dict):
        return [item for item in cast("dict[str, object]", value).values() if isinstance(item, str)]

    return []


def _toml_source_roots(toml: Path) -> list[str]:
    try:
        with toml.open("rb") as stream:
            data = cast("dict[str, object]", tomllib.load(stream))
    except OSError, tomllib.TOMLDecodeError:
        return []
    tools = _mapping(data.get("tool"))
    setuptools = _mapping(tools.get("setuptools"))
    find = _mapping(_mapping(setuptools.get("packages")).get("find"))
    backend = _mapping(_mapping(tools.get("uv")).get("build-backend"))

    return [
        *_strings(setuptools.get("package-dir")),
        *_strings(find.get("where")),
        *_strings(backend.get("module-root")),
    ]


def _config_source_roots(config: Path) -> list[str]:
    parser = configparser.ConfigParser()

    try:
        parser.read(config, encoding="utf-8")
    except OSError, UnicodeError, configparser.Error:
        return []
    names: list[str] = []

    for line in parser.get("options", "package_dir", fallback="").splitlines():
        _, separator, value = line.partition("=")
        name = (value if separator else line).strip()

        if name:
            names.append(name)

    return names


def _declared(root: Path) -> tuple[Path, ...]:
    names: list[str] = []
    toml = root / "pyproject.toml"
    config = root / "setup.cfg"

    if toml.is_file() and toml.stat().st_size <= MAX_CONFIGURATION_BYTES:
        names.extend(_toml_source_roots(toml))

    if config.is_file() and config.stat().st_size <= MAX_CONFIGURATION_BYTES:
        names.extend(_config_source_roots(config))

    return tuple(root / name for name in names if name not in {"", "."})


def import_roots(root: Path) -> tuple[Path, ...]:
    """Order a project's own import roots, keeping only directories that exist."""
    root = root.absolute()
    candidates = (root, *_declared(root), root / "src")

    return tuple(
        dict.fromkeys(
            candidate
            for candidate in candidates
            if candidate == root or (candidate.is_dir() and candidate.is_relative_to(root))
        )
    )
