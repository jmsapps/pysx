"""Scoped source imports compiled before Python builds lexical symbol tables."""

from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import marshal
import sys
import tokenize
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .compiler import VERSION, analyze

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import CodeType, ModuleType

    from .compiler import Compilation

_EXCLUDED = frozenset({".venv", "node_modules", "vendor", "site-packages", "__pycache__"})


def _digest(path: Path) -> str | None:
    try:
        with tokenize.open(path) as stream:
            source = stream.read(4 * 1024 * 1024 + 1)

        return hashlib.sha256(source.encode()).hexdigest()
    except OSError, UnicodeError, SyntaxError:
        return None


@dataclass(frozen=True)
class _Entry:
    code: CodeType
    dependencies: tuple[tuple[Path, str | None], ...]
    size: int


class CodeCache:
    """Bounded immutable code only; live application objects never enter this cache."""

    def __init__(self, *, entries: int = 128, bytes_limit: int = 8 * 1024 * 1024) -> None:
        self.entries = entries
        self.bytes_limit = bytes_limit
        self._codes: OrderedDict[tuple[str, str, str, tuple[Path, ...]], _Entry] = OrderedDict()

    def compile(
        self, source: str, filename: str, package: str, roots: tuple[Path, ...]
    ) -> CodeType:
        key = (filename, hashlib.sha256(source.encode()).hexdigest(), package, roots)
        existing = self._codes.get(key)

        if existing is not None and all(
            _digest(path) == digest for path, digest in existing.dependencies
        ):
            self._codes.move_to_end(key)

            return existing.code
        compilation = analyze(source, filename, workspace_roots=roots, package=package)
        code = compilation.code()
        size = len(source.encode()) + len(marshal.dumps(code))
        self._codes.pop(key, None)

        if size <= self.bytes_limit and self.entries > 0:
            while self._codes and (
                len(self._codes) >= self.entries
                or sum(entry.size for entry in self._codes.values()) + size > self.bytes_limit
            ):
                self._codes.popitem(last=False)
            self._codes[key] = _Entry(code, tuple(compilation.resolver.dependencies.items()), size)

        return code


class SourceLoader(importlib.machinery.SourceFileLoader):
    def __init__(self, fullname: str, path: str, finder: SourceFinder) -> None:
        super().__init__(fullname, path)
        self.finder = finder

    def get_code(self, fullname: str) -> CodeType:
        source = importlib.util.decode_source(self.get_data(self.path))
        package = fullname if self.is_package(fullname) else fullname.rpartition(".")[0]

        return self.finder.cache.compile(source, self.path, package, self.finder.roots)


class SourceFinder(importlib.abc.MetaPathFinder):
    def __init__(self, packages: tuple[str, ...], roots: tuple[Path, ...]) -> None:
        self.packages = packages
        self.roots = roots
        self.cache = CodeCache()
        self.compiler_version = VERSION

    def covers(self, name: str, path: Path) -> bool:
        if (
            name == "pysx"
            or name.startswith("pysx.")
            or any(part.startswith(".") or part in _EXCLUDED for part in path.parts)
        ):
            return False

        return any(
            name == prefix or name.startswith(prefix + ".") for prefix in self.packages
        ) or any(path.is_relative_to(root) for root in self.roots)

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        del target
        search = list(path) if path is not None else None
        spec = importlib.machinery.PathFinder.find_spec(fullname, search)

        if (
            spec is None
            or not isinstance(spec.loader, importlib.machinery.SourceFileLoader)
            or spec.origin is None
            or not self.covers(fullname, Path(spec.origin))
        ):
            return None
        spec.loader = SourceLoader(fullname, spec.origin, self)

        return spec


def install_loader(*, packages: tuple[str, ...] = (), roots: tuple[Path, ...] = ()) -> SourceFinder:
    """Install before importing application modules; repeat installs are idempotent."""
    normalized = tuple(root.resolve() for root in roots)

    if not packages and not normalized:
        raise ValueError("specify application packages or source roots")

    if any(not all(part.isidentifier() for part in name.split(".")) for name in packages):
        raise ValueError("invalid application package name")

    for finder in sys.meta_path:
        if (
            isinstance(finder, SourceFinder)
            and finder.packages == packages
            and finder.roots == normalized
        ):
            return finder
    finder = SourceFinder(packages, normalized)

    for name, module in tuple(sys.modules.items()):
        filename = getattr(module, "__file__", None)

        if (
            not isinstance(filename, str)
            or not filename.endswith(".py")
            or not finder.covers(name, Path(filename))
        ):
            continue
        loader = getattr(module, "__loader__", None)

        if isinstance(loader, SourceLoader):
            continue

        try:
            with tokenize.open(filename) as stream:
                compilation: Compilation = analyze(
                    stream.read(), filename, workspace_roots=normalized
                )
        except OSError, UnicodeError, SyntaxError:
            continue

        if compilation.calls:
            raise RuntimeError(
                f"install pysx loader before importing {name}; restart the application"
            )
    sys.meta_path.insert(0, finder)

    return finder


def import_app(name: str) -> ModuleType:
    """Infer the app scope without importing its parent, then import normally."""
    top = name.partition(".")[0]
    spec = importlib.machinery.PathFinder.find_spec(top)

    if spec is None:
        raise ModuleNotFoundError(name)
    roots = () if spec.origin is None else (Path(spec.origin).parent,)
    install_loader(packages=(top,), roots=roots)

    return importlib.import_module(name)
