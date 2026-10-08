"""Diagnostics for pysx templates, printed as JSON.

Columns are emitted as UTF-16 code units because that is what the editor
indexes by. Three unit systems meet here: ast.col_offset is UTF-8 bytes,
Python strings index codepoints, and the editor wants UTF-16 — an all-ASCII
test suite hides every one of these conversions.

Diagnostic positions are derived from raw source lines, never from decoded
Constant values: escapes and doubled braces change length against the source
and silently desync every range.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

from .compiler import analyze
from .compiler_imports import WorkspaceResolver
from .compiler_scope import Scope, ScopeIndex
from .parser import (
    Conditional,
    Element,
    Hole,
    HoleKind,
    LiteralText,
    Loop,
    Match,
    PysxSyntaxError,
    parse,
)
from .schema import normalize_attr
from .source_map import LiteralMapper, Positions

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from .compiler import Compilation

TAG_RE = re.compile(r"^\s*([A-Z][A-Za-z0-9_]*)")


def _utf16(line: str, index: int) -> int:

    return len(line[:index].encode("utf-16-le")) // 2


def _utf16_from_bytes(line: str, byte_col: int) -> int:
    prefix = line.encode("utf-8")[:byte_col].decode("utf-8", "ignore")

    return _utf16(line, len(prefix))


class Diagnostic(TypedDict):
    line: int
    startChar: int
    endChar: int
    message: str
    severity: Literal["error", "warning"]
    endLine: NotRequired[int]


def _d(
    line0: int,
    start: int,
    end: int,
    message: str,
    severity: Literal["error", "warning"] = "error",
) -> Diagnostic:

    return {
        "line": line0,
        "startChar": start,
        "endChar": end,
        "message": message,
        "severity": severity,
    }


def _bound_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)

        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"pysx", "render"}
        ):
            for keyword in node.keywords:
                if keyword.arg == "namespace" and isinstance(keyword.value, ast.Dict):
                    names.update(
                        key.value
                        for key in keyword.value.keys
                        if isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and key.value.isidentifier()
                    )

    return names


def _signal_names(tree: ast.AST) -> set[str]:
    out: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):

            continue

        fn = node.value.func

        if isinstance(fn, ast.Name) and fn.id in (
            "signal",
            "local_state",
            "structured",
            "derived",
            "eq",
            "ne",
            "lt",
            "le",
            "gt",
            "ge",
            "all_of",
            "any_of",
            "not_",
            "length",
            "contains",
            "concat",
        ):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))

    for _ in range(len(out) + 1):
        previous = out.copy()

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and _reactive_expression(node.value, out):
                out.update(t.id for t in node.targets if isinstance(t, ast.Name))

        if out == previous:

            break

    return out


def _reactive_expression(node: ast.AST, signals: set[str]) -> bool:
    if isinstance(node, ast.Name):

        return node.id in signals

    if isinstance(node, ast.Subscript):

        return _reactive_expression(node.value, signals)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):

        return _reactive_expression(node.left, signals) or _reactive_expression(node.right, signals)

    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], (ast.Lt, ast.LtE, ast.Gt, ast.GtE))
    ):

        return any(_reactive_expression(value, signals) for value in [node.left, *node.comparators])

    return False


def _reads_signal(node: ast.AST, signals: set[str]) -> bool:
    if isinstance(node, ast.Lambda):

        return False

    if isinstance(node, ast.Name) and node.id in signals:

        return True

    return any(_reads_signal(child, signals) for child in ast.iter_child_nodes(node))


def _snapshot_expression(node: ast.AST, signals: set[str]) -> bool:

    return isinstance(node, (ast.List, ast.Tuple, ast.ListComp)) and _reads_signal(node, signals)


def _snapshot_names(tree: ast.AST, signals: set[str]) -> set[str]:
    names: set[str] = set()
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)]

    for _ in range(len(assignments) + 1):
        previous = names.copy()

        for assignment in assignments:
            value = assignment.value

            if _snapshot_expression(value, signals) or (
                isinstance(value, ast.Name) and value.id in names
            ):
                names.update(
                    target.id for target in assignment.targets if isinstance(target, ast.Name)
                )

        if names == previous:

            break

    return names


def _operator_warnings(
    node: ast.AST, signals: set[str], *, root: ast.AST | None = None
) -> Iterator[tuple[ast.expr, str]]:
    # Deferred callbacks read signals deliberately; do not diagnose their bodies.

    if isinstance(node, ast.Lambda):

        return

    root = node if root is None else root

    message: str | None = None

    if isinstance(node, ast.BoolOp) and any(
        _reactive_expression(value, signals) for value in node.values
    ):
        message = "Python and/or coerces Signals; use all_of()/any_of()"
    elif (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.Not)
        and _reactive_expression(node.operand, signals)
    ):
        message = "Python not coerces a Signal; use not_()"
    elif isinstance(node, ast.Compare):
        operands = [node.left, *node.comparators]

        if len(node.ops) > 1 and any(_reactive_expression(value, signals) for value in operands):
            message = "Chained comparisons coerce Signals; use all_of(a < b, b < c)"
        elif any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops) and any(
            _reactive_expression(value, signals) for value in operands
        ):
            message = "Signal ==/!= compares identity; use eq()/ne() for reactive payload equality"
        elif any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops) and any(
            _reactive_expression(value, signals) for value in node.comparators
        ):
            message = "Python in cannot return a Signal; use contains(container, item)"
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id

        if name in signals and not node.args and not node.keywords and node is root:
            message = (
                f"{{{name}()}} is evaluated once and frozen; pass the signal "
                f"uncalled as {{{name}}} to make this slot reactive"
            )
        elif name in ("len", "bool") and node.args and _reactive_expression(node.args[0], signals):
            message = (
                "len(Signal) cannot return a Signal; use length()"
                if name == "len"
                else "bool(Signal) coerces truthiness; use reactive boolean helpers"
            )

    if message is not None:
        assert isinstance(node, ast.expr)
        yield node, message

        return

    for child in ast.iter_child_nodes(node):
        yield from _operator_warnings(child, signals, root=root)


def _span_diagnostic(node: ast.AST, lines: list[str], message: str) -> Diagnostic:
    row = getattr(node, "lineno", 1) - 1
    end_row = (getattr(node, "end_lineno", None) or row + 1) - 1
    start = _utf16_from_bytes(lines[row], getattr(node, "col_offset", 0))
    end = _utf16_from_bytes(lines[end_row], getattr(node, "end_col_offset", 0) or 0)
    diagnostic = _d(row, start, end, message)

    if end_row != row:
        diagnostic["endLine"] = end_row

    return diagnostic


def _styling_diagnostics(tree: ast.AST, lines: list[str]) -> list[Diagnostic]:
    out: list[Diagnostic] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):

            continue
        declaration = (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "styled"
        )
        extension = (
            isinstance(node.func, ast.Call)
            and isinstance(node.func.func, ast.Name)
            and node.func.func.id == "styled"
        )
        name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else "styled"
            if declaration or extension
            else ""
        )

        if name not in {"styled", "css", "global_style"} or not node.args:

            continue

        if name == "styled":
            base = (
                node.func.args[0]
                if extension and isinstance(node.func, ast.Call) and node.func.args
                else node.args[0]
            )

            if not declaration and not extension and isinstance(base, ast.Constant):
                out.append(
                    _span_diagnostic(
                        base,
                        lines,
                        "styled() requires an element, styled object or callable base; "
                        "strings are unsupported",
                    )
                )

            if not declaration and not extension and len(node.args) > 1:
                out.append(
                    _span_diagnostic(
                        node, lines, "styled(base, css) was removed; use styled(Base)(css)"
                    )
                )
            argument = node.args[0] if declaration or extension else None

            if (
                declaration
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "fragment"
            ):
                out.append(
                    _span_diagnostic(
                        node.func, lines, "styled.fragment has no element to carry a class"
                    )
                )
        else:
            argument = node.args[0]

        if not isinstance(argument, ast.TemplateStr):

            continue

        if name == "global_style":
            out.append(_span_diagnostic(argument, lines, "global_style() requires a plain string"))

            continue

        for value in argument.values:
            if isinstance(value, ast.Interpolation):
                metadata = value.conversion != -1 or value.format_spec is not None
                message = (
                    "CSS interpolation conversion/format metadata is unsupported; "
                    "use styleVars or runtime css"
                    if metadata
                    else "CSS interpolations are unsupported; use styleVars or runtime css"
                )
                out.append(_span_diagnostic(value, lines, message))

    return out


def _templates(tree: ast.AST) -> Iterator[ast.TemplateStr | ast.JoinedStr]:
    assignments = {
        target.id: node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    def resolve(
        expression: ast.expr, visited: frozenset[str] = frozenset()
    ) -> ast.TemplateStr | ast.JoinedStr | None:
        if isinstance(expression, (ast.TemplateStr, ast.JoinedStr)):

            return expression

        if isinstance(expression, ast.Name) and expression.id not in visited:
            value = assignments.get(expression.id)

            if value is not None:

                return resolve(value, visited | {expression.id})

        if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
            left, right = resolve(expression.left, visited), resolve(expression.right, visited)

            if isinstance(left, ast.TemplateStr) and isinstance(right, ast.TemplateStr):
                combined = ast.TemplateStr(values=[*left.values, *right.values])
                ast.copy_location(combined, left)
                combined.end_lineno, combined.end_col_offset = (
                    right.end_lineno,
                    right.end_col_offset,
                )

                return combined

        return None

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "pysx"
            and node.args
        ):
            arg = resolve(node.args[0])

            if isinstance(arg, ast.TemplateStr):
                yield arg
            elif isinstance(arg, ast.JoinedStr):
                # f-strings eagerly interpolate and cannot preserve live holes.
                yield arg


def template_fragments(node: ast.TemplateStr) -> tuple[str, ...]:
    strings = [""]

    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            strings[-1] += value.value
        elif isinstance(value, ast.Interpolation):
            strings.append("")

    return tuple(strings)


def _variant_keys(
    expression: ast.expr,
    scope: Scope,
    resolver: WorkspaceResolver,
    seen: frozenset[tuple[int, str]] = frozenset(),
) -> set[str] | None:
    """Unknown or competing declarations cannot prove a variant invalid."""
    key = (id(scope), ast.dump(expression))

    if key in seen or len(seen) >= 32:

        return None
    seen = seen | {key}

    if isinstance(expression, ast.Name):
        binding = scope.binding(expression.id)

        if isinstance(binding, ast.expr):

            return _variant_keys(binding, scope, resolver, seen)
    identity = scope.identity(expression)

    if identity is not None and not isinstance(expression, ast.Call):
        resolved = resolver.resolve(identity)

        if resolved is not None and isinstance(resolved[1], ast.expr):

            return _variant_keys(resolved[1], resolved[0].scope, resolver, seen)

    if not isinstance(expression, ast.Call):

        return None
    factory = expression.func
    identity = resolver.identity(scope.identity(factory))
    keys: set[str] | None = None

    if identity is not None and identity.startswith("pysx.styled."):
        from .schema import NATIVE_TAGS

        tag = identity.removeprefix("pysx.styled.").removeprefix("styled.")

        if tag in NATIVE_TAGS:
            keys = set()
    elif (
        isinstance(factory, ast.Call)
        and resolver.identity(scope.identity(factory.func)) in {"pysx.styled", "pysx.styled.styled"}
        and factory.args
    ):
        keys = _variant_keys(factory.args[0], scope, resolver, seen)

    if keys is None:

        return None

    for keyword in expression.keywords:
        if keyword.arg != "variants":

            continue

        if isinstance(keyword.value, ast.Constant) and keyword.value.value is None:

            continue

        if not isinstance(keyword.value, ast.Dict):

            return None

        for name in keyword.value.keys:
            if not isinstance(name, ast.Constant) or not isinstance(name.value, str):

                return None
            keys.add(name.value)

    return keys


def _legacy_diagnostics(
    source: str,
    filename: str,
    tree: ast.Module | None = None,
    compilation: Compilation | None = None,
) -> list[Diagnostic]:
    lines = source.split("\n")

    try:
        tree = tree if tree is not None else ast.parse(source, filename=filename)
    except SyntaxError as exc:
        row = max((exc.lineno or 1) - 1, 0)
        line = lines[row] if row < len(lines) else ""
        col = _utf16(line, max((exc.offset or 1) - 1, 0))
        message = exc.msg or "syntax error"

        if "lambda expressions are not allowed" in message:
            message = (
                "a bare lambda is not allowed in a t-string hole "
                "(':' starts a format spec) — wrap it in parentheses: "
                "{(lambda e: ...)}"
            )

        return [_d(row, col, max(col + 1, len(line)), message)]

    bound = _bound_names(tree)
    signals = _signal_names(tree)
    snapshots = _snapshot_names(tree, signals)
    out: list[Diagnostic] = _styling_diagnostics(tree, lines)
    resolver = compilation.resolver if compilation is not None else WorkspaceResolver(filename)
    scopes = ScopeIndex(tree, package=resolver.package)
    mapper = LiteralMapper(source)
    positions = Positions(source)

    for node in _templates(tree):
        if isinstance(node, ast.JoinedStr):
            line = lines[node.lineno - 1]
            col = _utf16_from_bytes(line, node.col_offset)
            out.append(
                _d(
                    node.lineno - 1,
                    col,
                    col + 4,
                    'pysx() needs a t-string; f"""..."""  interpolates eagerly and is not reactive',
                )
            )

            continue

        fragments = template_fragments(node)

        try:
            skeleton = parse(fragments)
        except PysxSyntaxError as exc:
            if exc.position is not None:
                try:
                    syntax_location = mapper.template(node).location(exc.position)
                except ValueError:
                    pass
                else:
                    syntax_start = positions.editor_position(syntax_location.start)
                    syntax_end = positions.editor_position(syntax_location.end)
                    item = _d(
                        syntax_start.line, syntax_start.character, syntax_end.character, str(exc)
                    )
                    item["endLine"] = syntax_end.line
                    out.append(item)

                    continue
            line = lines[node.lineno - 1]
            col = _utf16_from_bytes(line, node.col_offset)
            out.append(_d(node.lineno - 1, col, col + 4, str(exc)))

            continue

        interpolations = [value for value in node.values if isinstance(value, ast.Interpolation)]

        for index, kind, name in skeleton.holes:
            interpolation = interpolations[index]

            if (
                interpolation.conversion != -1 or interpolation.format_spec is not None
            ) and kind not in {HoleKind.TEXT, HoleKind.ATTR}:
                out.append(
                    _span_diagnostic(
                        interpolation,
                        lines,
                        f"{kind.value} interpolation conversion/format metadata is unsupported",
                    )
                )

            if kind is HoleKind.TAG:
                expression = interpolations[index].value

                if isinstance(expression, (ast.Constant, ast.List, ast.Dict, ast.Set, ast.Tuple)):
                    out.append(
                        _span_diagnostic(
                            expression, lines, "component tag requires an element or callable"
                        )
                    )

            if name is None or normalize_attr(name) not in {"css", "stylevars", "cssvars"}:

                continue
            css_interpolation = interpolations[index]

            if css_interpolation.conversion != -1 or css_interpolation.format_spec is not None:
                out.append(
                    _span_diagnostic(
                        css_interpolation,
                        lines,
                        "CSS attribute interpolation metadata is unsupported",
                    )
                )

        pending = list(skeleton.root)

        while pending:
            element = pending.pop()

            if isinstance(element, Conditional):
                pending.extend((*element.then, *element.otherwise))
            elif isinstance(element, Loop):
                pending.extend(element.children)
            elif isinstance(element, Match):
                pending.extend(child for case in element.cases for child in case.children)

            if not isinstance(element, Element):

                continue
            pending.extend(element.children)
            tag_expr = (
                interpolations[element.tag.index].value if isinstance(element.tag, Hole) else None
            )
            tag_name = (
                tag_expr.id
                if isinstance(tag_expr, ast.Name)
                else element.tag
                if isinstance(element.tag, str)
                else None
            )

            if tag_name is None:

                continue
            keys = _variant_keys(
                tag_expr if tag_expr is not None else ast.Name(tag_name, ast.Load()),
                scopes.nodes.get(node, scopes.nodes[tree]),
                resolver,
            )

            if keys is None:

                continue

            for attr, variant_value in element.attrs:
                if attr != "variant":

                    continue
                variant_expression = (
                    interpolations[variant_value.index].value
                    if isinstance(variant_value, Hole)
                    else None
                )
                constant = (
                    variant_expression.value
                    if isinstance(variant_expression, ast.Constant)
                    else variant_value
                    if isinstance(variant_value, str)
                    else None
                )

                if isinstance(constant, str) and constant not in keys:
                    message = f"unknown variant {constant!r}; declared variants: {sorted(keys)}"

                    if variant_expression is not None:
                        out.append(_span_diagnostic(variant_expression, lines, message))
                    elif isinstance(variant_value, LiteralText):
                        try:
                            span = mapper.template(node).location(
                                variant_value.span.start,
                                variant_value.span.end.offset - variant_value.span.start.offset,
                            )
                        except ValueError:

                            continue
                        begin = positions.editor_position(span.start)
                        finish = positions.editor_position(span.end)
                        diagnostic = _d(begin.line, begin.character, finish.character, message)
                        diagnostic["endLine"] = finish.line
                        out.append(diagnostic)

        # Unknown tags, located in raw source lines so ranges cannot desync.

        for row in range(node.lineno, node.end_lineno or node.lineno):
            if row >= len(lines):

                break
            match = TAG_RE.match(lines[row])

            if not match:

                continue
            tag = match.group(1)

            if tag in bound:

                continue
            start = _utf16(lines[row], match.start(1))
            out.append(
                _d(
                    row,
                    start,
                    start + len(tag),
                    f"unknown component {tag!r}: not defined in this module",
                )
            )

        # A called signal freezes at its first value, silently.

        for value in node.values:
            if not isinstance(value, ast.Interpolation):

                continue

            if _snapshot_expression(value.value, signals) or (
                isinstance(value.value, ast.Name) and value.value.id in snapshots
            ):
                warning = _span_diagnostic(
                    value.value,
                    lines,
                    "Sequence/comprehension content is a snapshot; source Signal changes "
                    "do not rebuild it. Use a live Signal source or a DSL loop.",
                )
                warning["severity"] = "warning"
                out.append(warning)

                continue

            for expression, message in _operator_warnings(value.value, signals):
                # Direct interpolation ranges retain braces for the existing editor fix.
                location = value if expression is value.value else expression
                row = location.lineno - 1
                line = lines[row]
                col = location.col_offset
                end_col = location.end_col_offset or col

                if (location.end_lineno or location.lineno) != location.lineno:
                    end_col = len(line)
                start = _utf16_from_bytes(line, col)
                end = _utf16_from_bytes(line, end_col)
                out.append(
                    _d(
                        row,
                        start,
                        end,
                        message,
                        severity="warning",
                    )
                )

    return out


def diagnostics_for_source(
    source: str,
    filename: str = "<template>",
    *,
    workspace_roots: tuple[Path, ...] = (),
    buffers: Mapping[str, str] | None = None,
    compilation: Compilation | None = None,
) -> list[Diagnostic]:
    """Current-buffer diagnostics and lexical tag ownership from the shared compiler."""
    compilation = compilation or analyze(
        source, filename, workspace_roots=workspace_roots, buffers=buffers
    )

    if compilation.source != source or compilation.filename != filename:

        raise ValueError("diagnostic compilation does not match source")
    positions = Positions(source)
    legacy = _legacy_diagnostics(source, filename, compilation.tree, compilation)

    if compilation.tree is None:

        return legacy
    # The old raw-line scan cannot see single-line, escaped or lexical tag bindings.
    # Preserve it only for explicit compatibility calls omitted by the compiler.
    compiled_lines = {
        row
        for call in compilation.calls
        for row in range(call.node.lineno - 1, call.node.end_lineno or call.node.lineno)
    }
    out = [
        item
        for item in legacy
        if not (item["line"] in compiled_lines and item["message"].startswith("unknown component"))
    ]

    for call in compilation.calls:
        for reference in call.references:
            if call.scope.owner(reference.name) is not None:

                continue
            start = positions.editor_position(reference.span.start)
            end = positions.editor_position(reference.span.end)
            item = _d(
                start.line,
                start.character,
                end.character,
                f"unknown component {reference.name!r}: no binding in this Python scope",
            )
            item["endLine"] = end.line
            out.append(item)

    for diagnostic in compilation.diagnostics:
        if any(
            "needs a t-string" in item["message"] and diagnostic.code == "template-source"
            for item in out
        ):

            continue
        start = positions.editor_position(diagnostic.span.start)
        end = positions.editor_position(diagnostic.span.end)
        item = _d(start.line, start.character, end.character, diagnostic.message, "warning")
        item["endLine"] = end.line

        if not any(existing["message"] == item["message"] for existing in out):
            out.append(item)

    return out


def diagnostics(path: Path) -> list[Diagnostic]:

    return diagnostics_for_source(path.read_text("utf-8"), str(path))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m pysx.check <file.py>", file=sys.stderr)

        return 2
    print(json.dumps(diagnostics(Path(sys.argv[1])), indent=None))

    return 0


if __name__ == "__main__":

    raise SystemExit(main())
