"""Bounded workspace AST lookup; resolving imports never executes packages."""

from __future__ import annotations

import ast
import copy
import hashlib
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .compiler_scope import Binding, Scope, ScopeIndex

if TYPE_CHECKING:
    from collections.abc import Mapping

MAX_MODULES = 32
MAX_MODULE_BYTES = 1_048_576


@dataclass(frozen=True)
class ModuleSource:
    name: str
    tree: ast.Module
    scope: Scope
    path: Path
    digest: str


def package_context(filename: str) -> tuple[Path, str]:
    path = Path(filename).absolute()
    parent = path.parent
    names: list[str] = []

    while parent.parent != parent and (parent / "__init__.py").is_file():
        names.insert(0, parent.name)
        parent = parent.parent

    return parent, ".".join(names)


class WorkspaceResolver:
    def __init__(
        self,
        filename: str,
        *,
        roots: tuple[Path, ...] = (),
        buffers: Mapping[str, str] | None = None,
        package: str | None = None,
    ) -> None:
        root, self.package = package_context(filename)

        if package is not None:
            self.package = package
        self.roots = tuple(dict.fromkeys((*roots, root, Path(filename).absolute().parent)))
        self.buffers = buffers or {}
        self.modules: dict[str, ModuleSource | None] = {}
        self.dependencies: dict[Path, str | None] = {}

    def module(self, name: str) -> ModuleSource | None:
        if name in self.modules:
            return self.modules[name]

        if len(self.modules) >= MAX_MODULES or not all(
            part.isidentifier() for part in name.split(".")
        ):
            return None
        self.modules[name] = None

        for root in self.roots:
            base = root.joinpath(*name.split("."))

            for path in (base.with_suffix(".py"), base / "__init__.py"):
                self.dependencies[path] = None
                buffered = self.buffers.get(str(path.absolute()))

                if buffered is None and (
                    not path.is_file() or path.stat().st_size > MAX_MODULE_BYTES
                ):
                    continue

                try:
                    if buffered is not None:
                        source = buffered
                    else:
                        with tokenize.open(path) as stream:
                            source = stream.read()

                    if len(source.encode()) > MAX_MODULE_BYTES:
                        return None
                    tree = ast.parse(source, filename=str(path))
                except OSError, UnicodeError, SyntaxError:
                    return None
                package = name if path.name == "__init__.py" else name.rpartition(".")[0]
                index = ScopeIndex(tree, package=package)
                digest = hashlib.sha256(source.encode()).hexdigest()
                self.dependencies[path] = digest
                module = ModuleSource(name, tree, index.nodes[tree], path, digest)
                self.modules[name] = module

                return module

        return None

    def resolve(
        self, identity: str, seen: frozenset[str] = frozenset()
    ) -> tuple[ModuleSource, Binding] | None:
        if identity in seen or identity.startswith("pysx."):
            return None
        name, _, symbol = identity.rpartition(".")
        module = self.module(name)

        if module is None:
            return None
        binding = module.scope.binding(symbol)

        if isinstance(binding, str):
            return self.resolve(binding, seen | {identity})

        if isinstance(binding, ast.Name):
            alias = module.scope.identity(binding)

            if alias is not None:
                return self.resolve(alias, seen | {identity})

        return module, binding

    def identity(self, identity: str | None, seen: frozenset[str] = frozenset()) -> str | None:
        if identity is None or identity in seen:
            return None

        if identity.startswith("pysx."):
            return identity
        name, _, symbol = identity.rpartition(".")
        module = self.module(name)

        if module is None:
            return identity
        binding = module.scope.binding(symbol)

        if isinstance(binding, str):
            return self.identity(binding, seen | {identity})

        if isinstance(binding, ast.expr):
            alias = module.scope.identity(binding)

            if alias is not None:
                return self.identity(alias, seen | {identity})

        return identity

    @staticmethod
    def qualified(expression: ast.expr, module: ModuleSource, alias: str) -> str:
        """Refer to module globals while keeping builtin/literal expressions intact."""

        class Qualify(ast.NodeTransformer):
            def visit_Name(self, node: ast.Name) -> ast.expr:
                if isinstance(node.ctx, ast.Load) and module.scope.owner(node.id) is not None:
                    value: ast.expr = ast.Attribute(
                        ast.Name(alias, ast.Load()), node.id, ast.Load()
                    )

                    return ast.copy_location(value, node)

                return node

        return ast.unparse(Qualify().visit(copy.deepcopy(expression)))
