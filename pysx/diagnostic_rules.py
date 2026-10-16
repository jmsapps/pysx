"""Provable advisory facts from shared syntax, schemas and workspace bindings."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .compiler import CompilerDiagnostic, native_base_for
from .forms import binding_control_error
from .parser import Conditional, Element, Hole, LiteralText, Loop, Match, parse
from .schema import (
    BINDING_ATTRS,
    NATIVE_TAGS,
    PSEUDO_ATTRS,
    RUNTIME_ATTRS,
    AttrFamily,
    allowed_attr,
    family_of,
    normalize_attr,
)

if TYPE_CHECKING:
    from .compiler import Compilation, TemplateCall
    from .compiler_imports import WorkspaceResolver
    from .compiler_scope import Scope
    from .parser import Node
    from .source_map import SourceSpan

_UNKNOWN = object()
_STYLED = {"pysx.styled", "pysx.styled.styled"}
_BINDINGS = frozenset(normalize_attr(name) for name in BINDING_ATTRS)


def _constant(expression: ast.expr, scope: Scope, seen: frozenset[str] = frozenset()) -> object:
    if len(seen) >= 32:
        return _UNKNOWN

    if isinstance(expression, ast.Constant):
        return expression.value

    if isinstance(expression, ast.Name) and expression.id not in seen:
        value, owner = scope.binding(expression.id), scope.owner(expression.id)

        if isinstance(value, ast.expr) and owner is not None:
            return _constant(value, owner, seen | {expression.id})

    return _UNKNOWN


def _value(value: str | Hole, call: TemplateCall) -> object:
    if isinstance(value, Hole):
        hole = call.source.holes[value.index]

        if hole.conversion != -1 or hole.format_spec is not None:
            return _UNKNOWN

        return _constant(hole.value, call.scope)

    return str(value)


def _signature(
    expression: ast.expr,
    scope: Scope,
    resolver: WorkspaceResolver,
    seen: frozenset[tuple[int, str]] = frozenset(),
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, int, frozenset[str], Scope | None, bool] | None:
    key = (id(scope), ast.dump(expression))

    if key in seen or len(seen) >= 32:
        return None
    seen = seen | {key}

    if isinstance(expression, ast.Name):
        binding = scope.binding(expression.id)
        owner = scope.owner(expression.id)

        if isinstance(binding, str):
            external = resolver.resolve(binding)

            if external is None:
                return None
            module, binding = external
            owner = module.scope

        if isinstance(binding, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if binding.decorator_list:
                return None

            return binding, 0, frozenset(), owner, False

        if isinstance(binding, ast.expr) and owner is not None:
            return _signature(binding, owner, resolver, seen)

    if isinstance(expression, ast.Call):
        identity = resolver.identity(scope.identity(expression.func))

        if identity == "functools.partial" and expression.args:
            if any(isinstance(arg, ast.Starred) for arg in expression.args) or any(
                kw.arg is None for kw in expression.keywords
            ):
                return None
            result = _signature(expression.args[0], scope, resolver, seen)

            if result is not None:
                function, count, keywords, annotation_scope, styled = result

                return (
                    function,
                    count + len(expression.args) - 1,
                    keywords | frozenset(kw.arg for kw in expression.keywords if kw.arg),
                    annotation_scope,
                    styled,
                )

        if (
            isinstance(expression.func, ast.Call)
            and resolver.identity(scope.identity(expression.func.func)) in _STYLED
            and expression.func.args
        ):
            result = _signature(expression.func.args[0], scope, resolver, seen)

            if result is not None:
                function, count, keywords, annotation_scope, _ = result

                return function, count, keywords, annotation_scope, True

        if isinstance(expression.func, ast.Name):
            binding = scope.binding(expression.func.id)

            if isinstance(binding, str):
                external = resolver.resolve(binding)
                binding = external[1] if external is not None else None

            if (
                isinstance(binding, ast.ClassDef)
                and not binding.bases
                and not binding.decorator_list
            ):
                method = next(
                    (
                        item
                        for item in binding.body
                        if isinstance(item, ast.FunctionDef) and item.name == "__call__"
                    ),
                    None,
                )

                if method is not None and not method.decorator_list:
                    return method, 1, frozenset(), None, False

    return None


def component_signature(
    expression: ast.expr,
    scope: Scope,
    resolver: WorkspaceResolver,
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, int, frozenset[str], Scope | None, bool] | None:
    """Share the conservative callable schema with editor features."""

    return _signature(expression, scope, resolver)


def _annotation_accepts(
    annotation: ast.expr | None, value: object, scope: Scope | None
) -> bool | None:
    if scope is None:
        return None

    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return None

    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        left = _annotation_accepts(annotation.left, value, scope)
        right = _annotation_accepts(annotation.right, value, scope)

        return (
            True
            if left is True or right is True
            else False
            if left is False and right is False
            else None
        )

    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return value is None

    if isinstance(annotation, ast.Name) and scope.owner(annotation.id) is None:
        if annotation.id == "object":
            return True
        classes: dict[str, type[object] | tuple[type[object], ...]] = {
            "str": str,
            "int": int,
            "float": (int, float),
            "bool": bool,
        }
        expected = classes.get(annotation.id)

        if expected is not None:
            return isinstance(value, expected)

    return None


def diagnostics_for_compilation(compilation: Compilation) -> list[CompilerDiagnostic]:
    findings: list[CompilerDiagnostic] = []

    for call in compilation.calls:
        def report(message: str, span: SourceSpan, code: str) -> None:
            findings.append(CompilerDiagnostic(message, span, code))

        for element in call.elements:
            if element.span is None:
                continue
            tag = element.tag
            expression = (
                ast.Name(tag, ast.Load())
                if isinstance(tag, str)
                else call.source.holes[tag.index].value
            )
            native = tag if isinstance(tag, str) and tag in NATIVE_TAGS else None
            native_base = (
                native_base_for(expression, call.scope, compilation.resolver)
                if native is None
                else None
            )

            if native_base is not None:
                native = native_base[0]
            origin = call.source.location(
                element.span.start, len(tag) if isinstance(tag, str) else 1
            )
            signature = (
                _signature(expression, call.scope, compilation.resolver) if native is None else None
            )
            supplied = {str(name) for name, _ in element.attrs}
            values = {normalize_attr(name): _value(value, call) for name, value in element.attrs}

            if element.children:
                supplied.add("children")

            if signature is not None:
                function, count, fixed, _, styled = signature
                args = function.args
                positional = [*args.posonlyargs, *args.args]
                required = {
                    arg.arg for arg in positional[count : len(positional) - len(args.defaults)]
                }
                required.update(
                    arg.arg
                    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
                    if default is None
                )

                for name in sorted(required - supplied - fixed):
                    report(
                        f"component is missing required prop {name!r}",
                        origin,
                        "component-required-prop",
                    )

                parameters = {arg.arg: arg for arg in (*args.args, *args.kwonlyargs)}
                accepts_children = args.vararg is not None and args.vararg.arg == "children"

                for name in sorted(
                    supplied - parameters.keys() - ({"variant"} if styled else set())
                ):
                    if args.kwarg is None and not (name == "children" and accepts_children):
                        report(
                            f"component does not accept prop {name!r}",
                            origin,
                            "component-unknown-prop",
                        )

            for name, value in element.attrs:
                if not isinstance(name, LiteralText):
                    continue
                attr_origin = call.source.location(name.span.start, len(name))
                normalized = normalize_attr(name)
                constant = _value(value, call)
                value_origin = (
                    call.source.hole_spans[value.index]
                    if isinstance(value, Hole)
                    else call.source.location(
                        value.span.start, max(1, value.span.end.offset - value.span.start.offset)
                    )
                    if isinstance(value, LiteralText)
                    else attr_origin
                )

                if native is not None:
                    if normalized == "variant" and native_base is not None and native_base[1]:
                        continue

                    if not allowed_attr(native, name):
                        report(
                            f"attribute {str(name)!r} is not valid on <{native}>; "
                            "use data-* for custom metadata",
                            attr_origin,
                            "native-attribute",
                        )

                        continue

                    if normalized in _BINDINGS:
                        control_type = values.get("type", "text")
                        multiple = values.get("multiple", False)
                        value_attr = values.get("value")
                        canonical = next(
                            binding
                            for binding in BINDING_ATTRS
                            if normalize_attr(binding) == normalized
                        )
                        error = binding_control_error(
                            canonical,
                            native,
                            control_type if isinstance(control_type, str) else None,
                            multiple if isinstance(multiple, bool) else None,
                            None if value_attr is _UNKNOWN else value_attr is not None,
                        )

                        if error is not None:
                            report(error, attr_origin, "binding-control")

                    if (
                        constant is _UNKNOWN
                        or normalized.startswith(("data-", "aria-"))
                        or normalized in PSEUDO_ATTRS | RUNTIME_ATTRS | {"class", "variant"}
                    ):
                        continue
                    family = family_of(name, native)

                    if normalized in _BINDINGS:
                        report(
                            f"{name} requires a writable binding, not a literal value",
                            value_origin,
                            "binding-value",
                        )
                    elif family is AttrFamily.EVENT:
                        report(
                            f"{name} requires a callable event handler, not a literal value",
                            value_origin,
                            "event-handler",
                        )
                    elif constant is not None:
                        valid = (
                            isinstance(constant, bool)
                            if family is AttrFamily.BOOLEAN
                            else isinstance(constant, (str, int, float))
                            if family is AttrFamily.NUMBER
                            else isinstance(constant, str)
                        )

                        if not valid:
                            report(
                                f"{name} expects a {family.value} value",
                                value_origin,
                                "native-value",
                            )
                elif signature is not None and constant is not _UNKNOWN:
                    function = signature[0]
                    parameter = next(
                        (
                            arg
                            for arg in (*function.args.args, *function.args.kwonlyargs)
                            if arg.arg == name
                        ),
                        None,
                    )

                    if (
                        parameter is not None
                        and _annotation_accepts(parameter.annotation, constant, signature[3])
                        is False
                    ):
                        report(
                            f"prop {str(name)!r} is incompatible with its declared annotation",
                            value_origin,
                            "component-prop-value",
                        )

        _unkeyed_forms(call, parse(call.source.strings).root, findings, compilation.resolver)

    return findings


def _unkeyed_forms(
    call: TemplateCall,
    nodes: tuple[Node, ...],
    findings: list[CompilerDiagnostic],
    resolver: WorkspaceResolver,
    *,
    unkeyed: bool = False,
) -> None:
    for node in nodes:
        if isinstance(node, Element):
            native = node.tag if isinstance(node.tag, str) and node.tag in NATIVE_TAGS else None

            if native is None:
                expression = (
                    ast.Name(node.tag, ast.Load())
                    if isinstance(node.tag, str)
                    else call.source.holes[node.tag.index].value
                )
                base = native_base_for(expression, call.scope, resolver)
                native = base[0] if base is not None else None

            for name, _ in node.attrs:
                if (
                    unkeyed
                    and native in {"input", "select", "textarea"}
                    and isinstance(name, LiteralText)
                    and normalize_attr(name) in _BINDINGS
                ):
                    findings.append(
                        CompilerDiagnostic(
                            "bound form controls in a loop need an explicit stable key",
                            call.source.location(name.span.start, len(name)),
                            "form-loop-key",
                        )
                    )
            _unkeyed_forms(call, node.children, findings, resolver, unkeyed=unkeyed)
        elif isinstance(node, Loop):
            _unkeyed_forms(
                call, node.children, findings, resolver, unkeyed=unkeyed or node.key is None
            )
        elif isinstance(node, Conditional):
            _unkeyed_forms(call, (*node.then, *node.otherwise), findings, resolver, unkeyed=unkeyed)
        elif isinstance(node, Match):
            for case in node.cases:
                _unkeyed_forms(call, case.children, findings, resolver, unkeyed=unkeyed)
