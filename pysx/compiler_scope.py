"""Conservative lexical identities for static template analysis.

Application modules are never imported. A name with competing writes has unknown
identity. This deliberately favors withholding a fix over guessing an import.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

Binding = str | ast.expr | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None


@dataclass(eq=False)
class Scope:
    parent: Scope | None = None
    kind: str = "module"
    bindings: dict[str, list[Binding]] = field(default_factory=lambda: dict[str, list[Binding]]())
    globals: set[str] = field(default_factory=lambda: set[str]())
    nonlocals: set[str] = field(default_factory=lambda: set[str]())
    type_only: set[str] = field(default_factory=lambda: set[str]())
    annotation_bindings: dict[str, list[Binding]] = field(default_factory=lambda: {})
    package: str = ""

    def owner(self, name: str) -> Scope | None:
        scope: Scope | None = self

        if name in self.globals:
            root = self

            while root.parent is not None:
                root = root.parent

            return root

        if name in self.nonlocals:
            scope = self.parent

        while scope is not None:
            if name in scope.bindings:

                return scope
            scope = scope.parent

        return None

    def binding(self, name: str) -> Binding:
        owner = self.owner(name)

        if owner is None:

            return None
        values = owner.bindings.get(name, [])

        return values[0] if len(values) == 1 else None

    def annotation_binding(self, name: str) -> Binding:
        """Type-only imports inform annotations, never executable tag bindings."""
        owner = self.owner(name)

        if owner is None or len(owner.bindings.get(name, [])) != 1:

            return None
        values = owner.annotation_bindings.get(name)

        return values[0] if values is not None and len(values) == 1 else self.binding(name)

    def identity(self, node: ast.expr, seen: frozenset[str] = frozenset()) -> str | None:
        if isinstance(node, ast.Name) and node.id not in seen:
            owner = self.owner(node.id)
            value = self.binding(node.id)

            if owner is None or node.id in owner.type_only:

                return None

            if isinstance(value, str):

                return value

            if isinstance(value, ast.expr):

                return owner.identity(value, seen | {node.id})
        elif isinstance(node, ast.Attribute):
            base = self.identity(node.value, seen)

            if base is not None:

                return base + "." + node.attr

        return None


class _Declarations(ast.NodeVisitor):
    def __init__(self, scope: Scope) -> None:
        self.scope = scope
        self.conditional = False
        self.type_only = False

    def add(self, name: str, value: Binding = None) -> None:
        self.scope.bindings.setdefault(name, []).append(None if self.conditional else value)

        if self.type_only:
            self.scope.type_only.add(name)
            self.scope.annotation_bindings.setdefault(name, []).append(value)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.add(
                alias.asname or alias.name.split(".")[0],
                alias.name if alias.asname else alias.name.split(".")[0],
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            module = node.module

            if node.level and self.scope.package:
                parts = self.scope.package.split(".")
                package = ".".join(parts[: len(parts) - node.level + 1])
                module = package + ("." + module if module else "")
            identity = (
                f"{module}.{alias.name}"
                if module and (not node.level or self.scope.package)
                else None
            )
            self.add(alias.asname or alias.name, identity)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.add(target.id, node.value)
            else:
                self.visit(target)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self.add(node.target.id, node.value)
        else:
            self.visit(node.target)

        if node.value is not None:
            self.visit(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.add(node.name, node)

        for expression in (*node.decorator_list, *node.args.defaults, *node.args.kw_defaults):
            if expression is not None:
                self.visit(expression)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.add(node.name, node)

        for expression in (*node.decorator_list, *node.args.defaults, *node.args.kw_defaults):
            if expression is not None:
                self.visit(expression)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.add(node.name, node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def visit_ListComp(self, node: ast.ListComp) -> None:
        # Comprehension targets do not declare names in the surrounding scope.
        self.visit(node.generators[0].iter)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self.visit(node.generators[0].iter)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self.visit(node.generators[0].iter)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self.visit(node.generators[0].iter)

    def visit_Global(self, node: ast.Global) -> None:
        self.scope.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.scope.nonlocals.update(node.names)

    def visit_If(self, node: ast.If) -> None:
        previous, previous_type = self.conditional, self.type_only
        self.conditional = True
        self.type_only = (isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING") or (
            isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING"
        )
        self.generic_visit(node)
        self.conditional, self.type_only = previous, previous_type

    def visit_Try(self, node: ast.Try) -> None:
        previous = self.conditional
        self.conditional = True
        self.generic_visit(node)
        self.conditional = previous

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None:
            self.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name is not None:
            self.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name is not None:
            self.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest is not None:
            self.add(node.rest)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        previous = self.conditional
        self.conditional = True
        self.generic_visit(node)
        self.conditional = previous

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        previous = self.conditional
        self.conditional = True
        self.generic_visit(node)
        self.conditional = previous

    def visit_While(self, node: ast.While) -> None:
        previous = self.conditional
        self.conditional = True
        self.generic_visit(node)
        self.conditional = previous


class ScopeIndex:
    def __init__(self, tree: ast.Module, *, package: str = "") -> None:
        self.nodes: dict[ast.AST, Scope] = {}
        root = Scope(package=package)
        self._scope(tree.body, root)
        self._walk(tree, root)

    @staticmethod
    def _scope(body: list[ast.stmt], scope: Scope) -> None:
        declarations = _Declarations(scope)

        for statement in body:
            declarations.visit(statement)

        for name in scope.globals | scope.nonlocals:
            values = scope.bindings.pop(name, [])
            owner = scope.owner(name)

            if owner is not None and values:
                owner.bindings.setdefault(name, []).extend([None] * len(values))

    def _walk(self, node: ast.AST, scope: Scope) -> None:
        self.nodes[node] = scope

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for expression in (*node.args.defaults, *node.args.kw_defaults):
                if expression is not None:
                    self._walk(expression, scope)

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    self._walk(decorator, scope)
            parent = scope.parent if scope.kind == "class" else scope
            child = Scope(parent, "function", package=scope.package)
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]

            for argument in arguments:
                child.bindings[argument.arg] = [None]

            for variadic in (node.args.vararg, node.args.kwarg):
                if variadic is not None:
                    child.bindings[variadic.arg] = [None]

            if isinstance(node, ast.Lambda):
                declarations = _Declarations(child)
                declarations.visit(node.body)
                self._walk(node.body, child)
            else:
                self._scope(node.body, child)

                for statement in node.body:
                    self._walk(statement, child)

            return

        if isinstance(node, ast.ClassDef):
            for expression in (*node.bases, *node.decorator_list):
                self._walk(expression, scope)
            child = Scope(scope, "class", package=scope.package)
            self._scope(node.body, child)

            for statement in node.body:
                self._walk(statement, child)

            return

        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            self._walk(node.generators[0].iter, scope)
            child = Scope(scope, "comprehension", package=scope.package)
            declarations = _Declarations(child)

            for generator in node.generators:
                declarations.visit(generator.target)
                self._walk(generator.target, child)

                if generator is not node.generators[0]:
                    self._walk(generator.iter, child)

                for condition in generator.ifs:
                    self._walk(condition, child)

            if isinstance(node, ast.DictComp):
                self._walk(node.key, child)
                self._walk(node.value, child)
            else:
                self._walk(node.elt, child)

            return

        for descendant in ast.iter_child_nodes(node):
            self._walk(descendant, scope)
