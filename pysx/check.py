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
from typing import TYPE_CHECKING, Literal, TypedDict

from .parser import PysxSyntaxError, parse

if TYPE_CHECKING:
    from collections.abc import Iterator

TAG_RE = re.compile(r"^\s*([A-Z][A-Za-z0-9_]*)")
BARE_CALL_RE = re.compile(r"^\s*(\w+)\s*\(\s*\)\s*$")


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


def _d(
    line0: int, start: int, end: int, message: str,
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
    return names


def _signal_names(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        fn = node.value.func
        if isinstance(fn, ast.Name) and fn.id in ("signal", "derived"):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
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
    out: list[Diagnostic] = []

    for node in _templates(tree):
        if isinstance(node, ast.JoinedStr):
            line = lines[node.lineno - 1]
            col = _utf16_from_bytes(line, node.col_offset)
            out.append(_d(
                node.lineno - 1, col, col + 4,
                'html() needs a t-string; f"""..."""  interpolates eagerly and is not reactive',
            ))
            continue

        fragments = tuple(
            v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
        try:
            parse(fragments)
        except PysxSyntaxError as exc:
            line = lines[node.lineno - 1]
            col = _utf16_from_bytes(line, node.col_offset)
            out.append(_d(node.lineno - 1, col, col + 4, str(exc)))
            continue

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
            out.append(_d(
                row, start, start + len(tag),
                f"unknown component {tag!r}: not defined in this module",
            ))

        # A called signal freezes at its first value, silently.
        for value in node.values:
            if not isinstance(value, ast.Interpolation):
                continue
            call = BARE_CALL_RE.match(value.str or "")
            if call and call.group(1) in signals:
                row = value.lineno - 1
                line = lines[row]
                start = _utf16_from_bytes(line, value.col_offset)
                end = _utf16_from_bytes(line, value.end_col_offset or value.col_offset)
                out.append(_d(
                    row, start, end,
                    f"{{{value.str}}} is evaluated once and frozen; pass the signal "
                    f"uncalled as {{{call.group(1)}}} to make this slot reactive",
                    severity="warning",
                ))

    return out


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m pysx.check <file.py>", file=sys.stderr)
        return 2
    print(json.dumps(diagnostics(Path(sys.argv[1])), indent=None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
