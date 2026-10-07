"""Shared, side-effect-free template binding and typed analysis compiler.

Runtime output uses the same component loads as the analysis projection. Prop
validation is emitted only into the projection. Authors always edit original source.
"""

from __future__ import annotations

import ast
import copy
import keyword
import tokenize
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .compiler_imports import ModuleSource, WorkspaceResolver
from .compiler_scope import Binding, Scope, ScopeIndex
from .parser import Conditional, Element, Hole, LiteralText, Loop, Match, PysxSyntaxError, parse
from .schema import NATIVE_TAGS, normalize_attr, python_attr, tag_info
from .source_map import LiteralMapper, MappedText, Positions, SourceSpan, TemplateSource, compose

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path
    from types import CodeType

    from .parser import Node

VERSION = "1"
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_PROJECTION_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class CompilerDiagnostic:
    message: str
    span: SourceSpan
    code: str


@dataclass(frozen=True)
class Reference:
    name: str
    span: SourceSpan
    role: str = "component"


@dataclass(frozen=True)
class TemplateCall:
    node: ast.Call
    source: TemplateSource
    elements: tuple[Element, ...]
    references: tuple[Reference, ...]
    scope: Scope


@dataclass(frozen=True)
class Compilation:
    source: str
    filename: str
    tree: ast.Module | None
    calls: tuple[TemplateCall, ...]
    diagnostics: tuple[CompilerDiagnostic, ...]
    helper: str
    import_offset: int
    resolver: WorkspaceResolver

    @property
    def complete(self) -> bool:

        return self.tree is not None and not self.diagnostics

    @property
    def references(self) -> tuple[Reference, ...]:

        return tuple(reference for call in self.calls for reference in call.references)

    def runtime_ast(self) -> ast.Module:
        if self.tree is None:

            raise SyntaxError("cannot compile incomplete Python source")
        tree = copy.deepcopy(self.tree)
        selected = {(call.node.lineno, call.node.col_offset): call for call in self.calls}
        helper = self.helper
        positions = Positions(self.source)

        def load(reference: Reference, call: TemplateCall) -> ast.expr:
            begin = positions.editor_position(reference.span.start)
            end = positions.editor_position(reference.span.end)
            name = ast.Name(reference.name, ast.Load())
            name.lineno, name.end_lineno = begin.line + 1, end.line + 1
            name.col_offset = len(
                self.source[positions.starts[begin.line] : reference.span.start].encode()
            )
            name.end_col_offset = len(
                self.source[positions.starts[end.line] : reference.span.end].encode()
            )

            if call.scope.kind == "class":

                return name
            getter = ast.Lambda(
                ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
                name,
            )
            result = ast.Call(
                ast.Attribute(ast.Name(helper, ast.Load()), "capture", ast.Load()),
                [ast.Constant(reference.name), getter],
                [],
            )

            return ast.copy_location(result, name)

        class Lower(ast.NodeTransformer):
            def visit_Call(self, node: ast.Call) -> ast.expr:
                original = (node.lineno, node.col_offset)
                self.generic_visit(node)
                call = selected.get(original)

                if call is None:

                    return node
                references = _unique(call.references)
                bindings = ast.Dict(
                    keys=[ast.Constant(ref.name) for ref in references],
                    values=[load(ref, call) for ref in references],
                )
                lowered = ast.Call(
                    ast.Attribute(ast.Name(helper, ast.Load()), "bind", ast.Load()),
                    [node, bindings],
                    [],
                )

                return ast.copy_location(lowered, node)

        Lower().visit(tree)

        if self.calls:
            statement = ast.Import(names=[ast.alias("pysx._compiler_runtime", helper)])
            prefix = import_index(tree)
            location = tree.body[prefix] if prefix < len(tree.body) else tree.body[-1]
            ast.copy_location(statement, location)
            tree.body.insert(prefix, statement)

        return ast.fix_missing_locations(tree)

    def code(self) -> CodeType:

        return compile(self.runtime_ast(), self.filename, "exec")

    def projection(self) -> MappedText:
        if not self.calls:

            return MappedText(
                self.source, tuple(SourceSpan(i, i + 1) for i in range(len(self.source)))
            )
        positions = Positions(self.source)
        insertions: dict[int, list[tuple[int, MappedText]]] = {}

        def add(offset: int, priority: int, value: MappedText) -> None:
            insertions.setdefault(offset, []).append((priority, value))

        dependencies: dict[str, str] = {}

        for call in self.calls:
            span = positions.ast_span(call.node)
            add(span.start, -span.end, _synthetic(f"{self.helper}.bind("))
            tail = _Text()
            tail.synthetic(", {")

            for reference in _unique(call.references):
                tail.synthetic(repr(reference.name) + ": ")
                tail.mapped(reference.name, reference.span)
                tail.synthetic(", ")
            tail.synthetic("}, (")

            for element in call.elements:
                _validate(
                    tail,
                    element,
                    call,
                    positions,
                    self.source,
                    self.helper,
                    self.resolver,
                    dependencies,
                )
            tail.synthetic("))")
            add(span.end, -span.start, tail.finish())
        imports = (
            f"import pysx._compiler_runtime as {self.helper}\n"
            f"import pysx.native as {self.helper}_native\n"
            + "".join(f"import {module} as {alias}\n" for module, alias in dependencies.items())
        )

        if self.import_offset and self.source[self.import_offset - 1] != "\n":
            imports = imports.replace("\n", "; ")
        add(self.import_offset, -1, _synthetic(imports))
        output = _Text()

        for offset in range(len(self.source) + 1):
            for _, insertion in sorted(insertions.get(offset, []), key=lambda item: item[0]):
                output.append(insertion)

            if offset < len(self.source):
                output.mapped(self.source[offset], SourceSpan(offset, offset + 1))
        result = output.finish()

        if len(result.text.encode()) > MAX_PROJECTION_BYTES:

            raise ValueError("analysis projection exceeds 16 MiB")
        ast.parse(result.text, filename=self.filename)

        return result


class _Text:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.origins: list[SourceSpan | None] = []

    def append(self, text: MappedText) -> None:
        self.parts.append(text.text)
        self.origins.extend(text.origins)

    def synthetic(self, text: str) -> None:
        self.append(_synthetic(text))

    def mapped(self, text: str, span: SourceSpan) -> None:
        origins = (
            tuple(SourceSpan(span.start + i, span.start + i + 1) for i in range(len(text)))
            if len(text) == span.end - span.start
            else (span,) * len(text)
        )
        self.append(MappedText(text, origins))

    def expression(self, node: ast.expr, positions: Positions, source: str) -> None:
        span = positions.ast_span(node)
        self.synthetic("(")
        self.mapped(source[span.start : span.end], span)
        self.synthetic(")")

    def finish(self) -> MappedText:

        return MappedText("".join(self.parts), tuple(self.origins))


def _synthetic(text: str) -> MappedText:

    return MappedText(text, (None,) * len(text))


def _unique(references: tuple[Reference, ...]) -> tuple[Reference, ...]:
    result: dict[str, Reference] = {}

    for reference in references:
        result.setdefault(reference.name, reference)

    return tuple(result.values())


def _elements(nodes: tuple[Node, ...]) -> Iterator[Element]:
    for node in nodes:
        if isinstance(node, Element):
            yield node
            yield from _elements(node.children)
        elif isinstance(node, Conditional):
            yield from _elements(node.then)
            yield from _elements(node.otherwise)
        elif isinstance(node, Loop):
            yield from _elements(node.children)
        elif isinstance(node, Match):
            for case in node.cases:
                yield from _elements(case.children)


def _component_tag(tag: str, scope: Scope) -> bool:
    # Foreign/unbound lowercase markup names do not introduce Python loads.

    return (
        tag not in NATIVE_TAGS
        and "-" not in tag
        and (tag[:1].isupper() or scope.owner(tag) is not None)
    )


def _native_base(
    expression: ast.expr,
    scope: Scope,
    resolver: WorkspaceResolver,
    seen: frozenset[str] = frozenset(),
) -> tuple[str, bool] | None:
    identity = scope.identity(expression)

    if identity is not None:
        if identity.startswith("pysx.elements."):
            tag = identity.rsplit(".", 1)[-1].rstrip("_")

            if tag in NATIVE_TAGS:

                return tag, False
        elif identity.startswith("pysx.native."):
            tag = identity.rsplit(".", 1)[-1].lower()

            if tag in NATIVE_TAGS:

                return tag, False
        elif identity.startswith("pysx.") and identity.count(".") == 1:
            tag = identity.split(".")[1]

            if tag in NATIVE_TAGS and tag != "html":

                return tag, False
        elif identity not in seen:
            external = resolver.resolve(identity)

            if external is not None and isinstance(external[1], ast.expr):

                return _native_base(external[1], external[0].scope, resolver, seen | {identity})

    if isinstance(expression, ast.Name) and expression.id not in seen:
        value = scope.binding(expression.id)
        owner = scope.owner(expression.id)

        if isinstance(value, ast.expr) and owner is not None:

            return _native_base(value, owner, resolver, seen | {expression.id})

    if isinstance(expression, ast.Call):
        identity = scope.identity(expression.func)
        factory = (
            scope.binding(expression.func.id) if isinstance(expression.func, ast.Name) else None
        )
        factory_scope = scope

        if isinstance(factory, str):
            external = resolver.resolve(factory)

            if external is not None:
                factory_scope, factory = external[0].scope, external[1]

        if isinstance(factory, ast.FunctionDef):
            annotation = factory.returns

            if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
                annotation = (
                    ast.Name(annotation.value, ast.Load())
                    if annotation.value.isidentifier()
                    else None
                )
            returned = (
                factory_scope.annotation_binding(annotation.id)
                if isinstance(annotation, ast.Name)
                else None
            )

            if isinstance(returned, str) and returned.startswith("pysx.styled_native.Styled"):
                tag = returned.removeprefix("pysx.styled_native.Styled").lower()

                if tag in NATIVE_TAGS:

                    return tag, True

        if identity is not None and identity.startswith("pysx.styled."):
            tag = identity.rsplit(".", 1)[-1]

            if tag in NATIVE_TAGS:

                return tag, True

        if (
            isinstance(expression.func, ast.Call)
            and scope.identity(expression.func.func) == "pysx.styled"
            and expression.func.args
        ):
            base = _native_base(expression.func.args[0], scope, resolver, seen)

            if base is not None:

                return base[0], True

    return None


def native_tag_for(tag: str, scope: Scope, resolver: WorkspaceResolver) -> str | None:
    """Shared native/styled schema query for editor sites."""

    if tag in NATIVE_TAGS:

        return tag
    base = _native_base(ast.Name(tag, ast.Load()), scope, resolver)

    return base[0] if base is not None else None


def import_index(tree: ast.Module) -> int:
    index = 0

    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        index = 1

    while index < len(tree.body):
        statement = tree.body[index]

        if not isinstance(statement, ast.ImportFrom) or statement.module != "__future__":

            break
        index += 1

    return index


def _unknown_partial(name: str, scope: Scope, resolver: WorkspaceResolver) -> bool:
    binding = scope.binding(name)

    if isinstance(binding, str):
        external = resolver.resolve(binding)

        if external is not None:
            scope, binding = external[0].scope, external[1]

    if not isinstance(binding, ast.Call) or scope.identity(binding.func) != "functools.partial":

        return False

    if (
        not binding.args
        or any(kw.arg is None for kw in binding.keywords)
        or any(isinstance(arg, ast.Starred) for arg in binding.args)
    ):

        return True
    target = binding.args[0]

    if isinstance(target, ast.Name):
        base = scope.binding(target.id)

        if isinstance(base, ast.Call) and scope.identity(base.func) == "functools.partial":

            return True

    return False


def _assembly(
    node: ast.expr, scope: Scope, mapper: LiteralMapper, seen: frozenset[str] = frozenset()
) -> TemplateSource:
    if isinstance(node, ast.TemplateStr):

        return mapper.template(node)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):

        return compose(
            _assembly(node.left, scope, mapper, seen), _assembly(node.right, scope, mapper, seen)
        )

    if isinstance(node, ast.Name) and node.id not in seen and scope.owner(node.id) is scope:
        binding = scope.binding(node.id)

        if (
            isinstance(binding, ast.expr)
            and mapper.positions.ast_span(binding).end <= mapper.positions.ast_span(node).start
        ):

            return _assembly(binding, scope, mapper, seen | {node.id})

    raise ValueError(
        "component origin unavailable: use a literal or a bound Fragment across scopes"
    )


def analyze(
    source: str,
    filename: str = "<template>",
    *,
    workspace_roots: tuple[Path, ...] = (),
    buffers: Mapping[str, str] | None = None,
    package: str | None = None,
) -> Compilation:
    if len(source.encode()) > MAX_SOURCE_BYTES:

        raise ValueError("Python source exceeds 4 MiB")
    positions = Positions(source)
    resolver = WorkspaceResolver(filename, roots=workspace_roots, buffers=buffers, package=package)

    try:
        tree = ast.parse(source, filename=filename)
        mapper = LiteralMapper(source)
    except (SyntaxError, tokenize.TokenError) as error:
        message = str(error)
        start = 0

        if isinstance(error, SyntaxError):
            row = min(max(1, error.lineno or 1), len(positions.starts))
            start = positions.text_offset(row, max(0, (error.offset or 1) - 1))
            start = min(start, max(0, len(source) - 1))

        return Compilation(
            source,
            filename,
            None,
            (),
            (
                CompilerDiagnostic(
                    message, SourceSpan(start, min(start + 1, len(source))), "python-syntax"
                ),
            ),
            "",
            0,
            resolver,
        )
    scopes = ScopeIndex(tree, package=resolver.package)
    calls: list[TemplateCall] = []
    diagnostics: list[CompilerDiagnostic] = []
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names.update(
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    )
    names.update(node.arg for node in ast.walk(tree) if isinstance(node, ast.arg))
    names.update(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )
    helper = "_pysx_compiler"

    while any(name == helper or name.startswith(helper + "_") for name in names):
        helper += "_"

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):

            continue
        scope = scopes.nodes[node]

        if resolver.identity(scope.identity(node.func)) not in {"pysx.html", "pysx.render.html"}:
            marker_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.value.id
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                else None
            )
            owner = scope.owner(marker_name) if marker_name is not None else None

            if owner is not None and marker_name is not None:
                history = owner.bindings.get(marker_name, [])

                if len(history) > 1 and any(
                    isinstance(value, str)
                    and value in {"pysx.html", "pysx.render.html", "pysx", "pysx.render"}
                    for value in history
                ):
                    diagnostics.append(
                        CompilerDiagnostic(
                            "template marker has competing bindings; "
                            "component usage cannot be established safely",
                            positions.ast_span(node.func),
                            "ambiguous-marker",
                        )
                    )

            continue
        # Compatibility inventories contain actual Python references and may use
        # names deliberately different from their markup spelling.

        if any(kw.arg in {"use", "namespace"} for kw in node.keywords):

            continue
        argument = (
            node.args[0]
            if node.args
            else next((kw.value for kw in node.keywords if kw.arg == "template"), None)
        )

        if argument is None:
            diagnostics.append(
                CompilerDiagnostic(
                    "html requires a template", positions.ast_span(node), "template-source"
                )
            )

            continue

        template: TemplateSource | None = None

        try:
            template = _assembly(argument, scope, mapper)
            elements = tuple(_elements(parse(template.strings).root))
            references: list[Reference] = []

            for element in elements:
                for attr, value in element.attrs:
                    if isinstance(attr, LiteralText):
                        template.location(attr.span.start, len(attr))

                    if isinstance(value, LiteralText):
                        template.location(
                            value.span.start, value.span.end.offset - value.span.start.offset
                        )

                if not isinstance(element.tag, str) or not _component_tag(element.tag, scope):

                    continue

                if not element.tag.isidentifier() or keyword.iskeyword(element.tag):

                    raise ValueError(f"component tag {element.tag!r} is not a Python name")

                if element.span is not None:
                    location = template.location(element.span.start, len(element.tag))
                    references.append(Reference(element.tag, location))
                    owner = scope.owner(element.tag)

                    if _unknown_partial(element.tag, scope, resolver):
                        diagnostics.append(
                            CompilerDiagnostic(
                                "partial schema is unavailable for nested or unpacked arguments; "
                                "use a typed component wrapper to retain prop checking",
                                location,
                                "partial-schema-unknown",
                            )
                        )

                    if owner is not None and element.tag in owner.type_only:
                        diagnostics.append(
                            CompilerDiagnostic(
                                "type-only imports cannot bind runtime components",
                                location,
                                "type-only-component",
                            )
                        )
            calls.append(TemplateCall(node, template, elements, tuple(references), scope))
        except (ValueError, PysxSyntaxError) as error:
            location = positions.ast_span(argument)

            if (
                isinstance(error, PysxSyntaxError)
                and error.position is not None
                and template is not None
            ):
                location = template.location(error.position)
            diagnostics.append(CompilerDiagnostic(str(error), location, "template-source"))
    prefix = import_index(tree)
    import_offset = 0

    if prefix:
        import_offset = (
            positions.ast_span(tree.body[prefix]).start if prefix < len(tree.body) else len(source)
        )

    return Compilation(
        source, filename, tree, tuple(calls), tuple(diagnostics), helper, import_offset, resolver
    )


def _validate(
    text: _Text,
    element: Element,
    call: TemplateCall,
    positions: Positions,
    source: str,
    helper: str,
    resolver: WorkspaceResolver,
    dependencies: dict[str, str],
) -> None:
    tag = element.tag

    if isinstance(tag, str) and _unknown_partial(tag, call.scope, resolver):

        return

    if isinstance(tag, str) and tag not in NATIVE_TAGS and not _component_tag(tag, call.scope):

        return
    native = isinstance(tag, str) and tag in NATIVE_TAGS
    origin = (
        call.source.location(element.span.start, len(tag))
        if isinstance(tag, str) and element.span
        else call.source.span
    )
    target: ast.expr | None = None
    fixed_args: list[ast.expr] = []
    fixed_kwargs: dict[str, ast.expr] = {}
    styled_callable = False
    styled_native = False
    expression = (
        ast.Name(tag, ast.Load()) if isinstance(tag, str) else call.source.holes[tag.index].value
    )
    declaration: Binding = (
        call.scope.binding(expression.id) if isinstance(expression, ast.Name) else None
    )
    binding_scope = call.scope
    binding_module: ModuleSource | None = None

    if isinstance(declaration, str):
        external = resolver.resolve(declaration)

        if external is not None:
            binding_module, declaration = external
            binding_scope = binding_module.scope

    def emit_expression(value: ast.expr) -> None:
        if binding_module is None:
            text.expression(value, positions, source)
        else:
            alias = dependencies.setdefault(binding_module.name, f"{helper}_dep{len(dependencies)}")
            text.synthetic("(" + resolver.qualified(value, binding_module, alias) + ")")

    resolved_native = _native_base(expression, call.scope, resolver) if not native else None

    if resolved_native is not None:
        tag, styled_native = resolved_native
        native = True

    if not native:
        binding = declaration

        if (
            isinstance(binding, ast.Call)
            and binding_scope.identity(binding.func) == "functools.partial"
            and binding.args
        ):
            target, fixed_args = binding.args[0], binding.args[1:]
            fixed_kwargs = {kw.arg: kw.value for kw in binding.keywords if kw.arg is not None}
        elif (
            isinstance(binding, ast.Call)
            and isinstance(binding.func, ast.Call)
            and binding_scope.identity(binding.func.func) == "pysx.styled"
            and binding.func.args
        ):
            target = binding.func.args[0]
            styled_callable = True

    if isinstance(tag, str) and "-" in tag:

        return

    text.synthetic(f"{helper}.component_result(")

    if target is not None:
        emit_expression(target)
    elif native and isinstance(tag, str) and tag in NATIVE_TAGS:
        text.mapped(f"{helper}_native.{tag.capitalize()}", origin)
    elif isinstance(tag, Hole):
        text.synthetic("(")
        text.expression(call.source.holes[tag.index].value, positions, source)
        text.synthetic(")")
    else:
        text.mapped(tag, origin)
    text.synthetic("(")

    for argument in fixed_args:
        emit_expression(argument)
        text.synthetic(", ")
    properties: list[tuple[str, str | Hole | ast.expr]] = [
        (name, value) for name, value in fixed_kwargs.items() if name not in dict(element.attrs)
    ]
    properties.extend(element.attrs)

    if element.children:
        var_children = native

        if isinstance(target, ast.Name):
            declaration = binding_scope.binding(target.id)

        if isinstance(declaration, ast.Call) and isinstance(declaration.func, ast.Name):
            candidate = binding_scope.binding(declaration.func.id)

            if isinstance(candidate, str):
                external = resolver.resolve(candidate)
                candidate = external[1] if external is not None else None

            if isinstance(candidate, ast.ClassDef):
                declaration = next(
                    (
                        method
                        for method in candidate.body
                        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and method.name == "__call__"
                    ),
                    None,
                )

        if isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef)):
            var_children = (
                declaration.args.vararg is not None and declaration.args.vararg.arg == "children"
            )
        text.synthetic(("" if var_children else "children=") + f"{helper}.children(), ")

    custom_properties: list[tuple[str, str | Hole | ast.expr]] = []

    for name, value in properties:
        if (styled_callable or styled_native) and name == "variant":

            continue

        if element.children and name == "children":

            continue

        info = tag_info(tag) if isinstance(tag, str) and native else None

        if native and (
            python_attr(name).startswith(("data_", "aria_"))
            or normalize_attr(name) == "class"
            or (
                info is not None
                and normalize_attr(name) not in info.attributes
                and not python_attr(name).startswith("on_")
                and normalize_attr(name)
                not in {
                    "bindvalue",
                    "bindchecked",
                    "bindselected",
                    "ref",
                    "css",
                    "stylevars",
                    "cssvars",
                }
            )
        ):
            custom_properties.append((name, value))

            continue
        spelling = python_attr(name) if native else name
        unpacked = not spelling.isidentifier() or keyword.iskeyword(spelling)

        if unpacked:
            text.synthetic("**{")
            spelling = repr(spelling)

        if isinstance(name, LiteralText):
            text.mapped(spelling, call.source.location(name.span.start, len(name)))
        else:
            text.synthetic(spelling)
        text.synthetic(": " if unpacked else "=")

        if isinstance(value, Hole):
            text.expression(call.source.holes[value.index].value, positions, source)
        elif isinstance(value, ast.expr):
            emit_expression(value)
        elif isinstance(value, LiteralText):
            text.mapped(
                repr(str(value)),
                call.source.location(
                    value.span.start, max(1, value.span.end.offset - value.span.start.offset)
                ),
            )
        else:
            text.synthetic(repr(value))
        text.synthetic("}, " if unpacked else ", ")

    if custom_properties:
        text.synthetic("custom_attrs={")

        for name, value in custom_properties:
            text.synthetic(repr(name) + ": ")

            if isinstance(value, Hole):
                text.expression(call.source.holes[value.index].value, positions, source)
            elif isinstance(value, ast.expr):
                emit_expression(value)
            else:
                text.synthetic(repr(str(value)))
            text.synthetic(", ")
        text.synthetic("}, ")
    text.synthetic(")), ")

    if styled_callable or styled_native:
        for name, value in element.attrs:
            if name == "variant":
                text.synthetic(f"{helper}.variant(")

                if isinstance(value, Hole):
                    text.expression(call.source.holes[value.index].value, positions, source)
                else:
                    text.synthetic(repr(str(value)))
                text.synthetic("), ")
