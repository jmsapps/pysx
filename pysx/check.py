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

from .parser import PysxSyntaxError, parse
from .schema import normalize_attr

if TYPE_CHECKING:
    from collections.abc import Iterator

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
            and node.func.id in {"html", "render"}
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
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        name = node.func.id

        if name not in {"styled", "css", "global_style"} or not node.args:
            continue

        if name == "styled":
            base = node.args[0]

            if isinstance(base, ast.Constant):
                out.append(
                    _span_diagnostic(
                        base,
                        lines,
                        "styled() requires an element, styled object or callable base; "
                        "strings are unsupported",
                    )
                )
            argument = node.args[1] if len(node.args) > 1 else None
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
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "html"
            and node.args
        ):
            arg = node.args[0]
            if isinstance(arg, ast.TemplateStr):
                yield arg
            elif isinstance(arg, ast.JoinedStr):
                # f"""...""" eagerly interpolates; it is silently not a template.
                yield arg


def diagnostics(path: Path) -> list[Diagnostic]:
    source = path.read_text("utf-8")
    lines = source.split("\n")

    try:
        tree = ast.parse(source, filename=str(path))
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
    out: list[Diagnostic] = _styling_diagnostics(tree, lines)

    for node in _templates(tree):
        if isinstance(node, ast.JoinedStr):
            line = lines[node.lineno - 1]
            col = _utf16_from_bytes(line, node.col_offset)
            out.append(
                _d(
                    node.lineno - 1,
                    col,
                    col + 4,
                    'html() needs a t-string; f"""..."""  interpolates eagerly and is not reactive',
                )
            )
            continue

        fragments = tuple(
            v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
        try:
            skeleton = parse(fragments)
        except PysxSyntaxError as exc:
            line = lines[node.lineno - 1]
            col = _utf16_from_bytes(line, node.col_offset)
            out.append(_d(node.lineno - 1, col, col + 4, str(exc)))
            continue

        interpolations = [value for value in node.values if isinstance(value, ast.Interpolation)]

        for index, _kind, name in skeleton.holes:
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


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m pysx.check <file.py>", file=sys.stderr)
        return 2
    print(json.dumps(diagnostics(Path(sys.argv[1])), indent=None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
