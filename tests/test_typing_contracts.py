"""Strict checker fixtures; intentional invalid programs remain text until execution."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

POSITIVE = '''from typing import assert_type
from pysx import Each, Fragment, Signal, component, derived, each, html, signal
from pysx.wire import Op, PatchMessage

count = signal(1)
assert_type(count, Signal[int])
assert_type(count(), int)
label = derived(lambda: str(count()))
assert_type(label, Signal[str])
rows = signal([1, 2])

@component
def item(value: int) -> Fragment:
    return html(t"""\n    div: {value}\n""")

assert_type(item(1), Fragment)
spec = each(rows, item, key=lambda value: value)
assert_type(spec, Each[int])
ops: list[Op] = [{"op": "attr", "id": "e1", "name": "value", "v": None}]
patch: PatchMessage = {"t": "patch", "ops": ops}
'''


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
@pytest.mark.parametrize(
    ("source", "error"),
    [
        pytest.param(POSITIVE, None, id="inference"),
        pytest.param(
            'from typing import assert_type\n'
            'from pysx import structured, list_index, dict_key, Projection, Structured\n'
            'root = structured({"rows": [1, 2]})\n'
            'assert_type(root, Structured[dict[str, list[int]]])\n'
            'rows = dict_key(root, "rows")\n'
            'assert_type(rows, Projection[list[int]])\n'
            'item = list_index(rows, 0)\n'
            'assert_type(item, Projection[int])\nitem.set(3)\n',
            None,
            id="structured_state_inference",
        ),
        pytest.param(
            'from pysx import structured, list_index\nitem = list_index(structured([1]), 0)\n'
            'item.set("bad")\n',
            "set",
            id="structured_state_payload",
        ),
        pytest.param(
            'from typing import assert_type\n'
            'from pysx import Signal, signal, derived\n'
            'from collections.abc import Callable\n'
            'n = signal(1)\ns = signal("a")\n'
            'out = derived(lambda: s() * n())\n'
            'assert_type(out, Signal[str])\n'
            'assert_type(out.subscribe(lambda value: value.upper()), Callable[[], None])\n',
            None,
            id="identity_settled_inference",
        ),
        pytest.param(
            'from pysx import signal\ncount = signal(1)\n'
            'count.subscribe(lambda value: value.upper())\n',
            "upper",
            id="identity_settled_subscriber_payload",
        ),
        pytest.param(
            'from pysx import signal\ncount = signal(1)\ncount.set("wrong")\n',
            "set",
            id="payload",
        ),
        pytest.param(
            'from pysx.wire import TextOp\nop: TextOp = {"op": "wrong", "id": "x", "v": "x"}\n',
            "Literal",
            id="discriminant",
        ),
        pytest.param(
            'from pysx.wire import TextOp\nop: TextOp = {"op": "text", "id": "x", "v": None}\n',
            "None",
            id="text-value",
        ),
    ],
)
def test_public_api_contracts(
    tmp_path: Path,
    checker: str,
    source: str,
    error: str | None,
) -> None:
    fixture = tmp_path / "contract.py"
    fixture.write_text(source)
    command = [sys.executable, "-m", checker]
    if checker == "mypy":
        command += ["--strict", "--show-error-codes"]
    command.append(str(fixture))
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout + result.stderr
    if error is None:
        assert result.returncode == 0, output
    else:
        assert result.returncode == 1, output
        assert error in output, output
        assert str(fixture) in output, output
        line = 3 if error in ("set", "upper") else 2
        assert f"{fixture}:{line}:" in output, output
        expected_count = 3 if checker == "pyright" and error == "upper" else 1
        diagnostic = " - error:" if checker == "pyright" else ": error:"
        assert output.count(diagnostic) == expected_count, output
        expected = "[attr-defined]" if error == "upper" else (
            "[arg-type]" if error == "set" else "[typeddict-item]"
        )
        if checker == "mypy":
            assert expected in output, output
        else:
            expected = "reportAttributeAccessIssue" if error == "upper" else (
                "reportArgumentType" if error == "set" else "reportAssignmentType"
            )
            assert expected in output, output


operator_positive = """from typing import assert_type
from tests.prototypes.operators import (
    Signal, all_of, any_of, not_, eq, ne, lt, le, gt, ge,
    concat, contains, length, inclusive_range, inclusive_enum_range,
)
n = Signal(1)
m = Signal(2)
f = Signal(1.5)
s = Signal("a")
b = Signal(True)
"""
for name in ("eq", "ne", "lt", "le", "gt", "ge"):
    for arguments in ("n, 2", "2, n", "n, m", 's, "b"', '"b", s'):
        operator_positive += f"assert_type({name}({arguments}), Signal[bool])\n"
for expression in (
    "n < 2",
    "n <= 2",
    "n > 2",
    "n >= 2",
    "2 < n",
    "2 <= n",
    "2 > n",
    "2 >= n",
    's < "b"',
    '"b" > s',
    "n < 2.5",
    "2 < f",
    "b & False",
    "False & b",
    "b | True",
    "True | b",
    "~b",
    "all_of(b, True)",
    "any_of(False, b)",
    "not_(b)",
):
    operator_positive += f"assert_type({expression}, Signal[bool])\n"
operator_positive += """assert_type(n == m, bool)
assert_type(2 == n, bool)
assert_type(n != m, bool)
assert_type(bool(b), bool)
assert_type(concat(s, n), Signal[str])
rows = Signal([1, 2])
assert_type(length(rows), Signal[int])
assert_type(len(rows.get()), int)
assert_type(1 in rows.get(), bool)
assert_type(contains(rows, n), Signal[bool])
assert_type(contains(Signal((1, 2)), n), Signal[bool])
assert_type(contains(Signal({1, 2}), n), Signal[bool])
assert_type(contains(Signal(frozenset({1, 2})), n), Signal[bool])
assert_type(contains((1, 2), n), Signal[bool])
assert_type(contains({1, 2}, n), Signal[bool])
assert_type(contains(frozenset({1, 2}), n), Signal[bool])
assert_type(contains(s, "a"), Signal[bool])
assert_type(contains("abc", s), Signal[bool])
assert_type(contains(Signal(inclusive_range(1, 3)), n), Signal[bool])
assert_type(contains(inclusive_range(1, 3), n), Signal[bool])
assert_type(inclusive_range("a", "c"), tuple[str, ...])
assert_type(contains(Signal(inclusive_range("a", "c")), "b"), Signal[bool])
from enum import Enum
class Ordinal(Enum):
    FIRST = 1
    LAST = 2
assert_type(inclusive_enum_range(Ordinal.FIRST, Ordinal.LAST), tuple[Ordinal, ...])
assert_type(contains(Signal(inclusive_enum_range(Ordinal.FIRST, Ordinal.LAST)),
                     Ordinal.FIRST), Signal[bool])
"""


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
@pytest.mark.parametrize(
    ("source", "mypy_code", "pyright_code"),
    [
        pytest.param(operator_positive, None, None, id="operator_inference"),
        pytest.param(
            'from tests.prototypes.operators import Signal, eq\neq(Signal(1), "bad")\n',
            "[misc]",
            "reportCallIssue",
            id="equality_mismatch",
        ),
        pytest.param(
            'from tests.prototypes.operators import Signal\nSignal(1) < "bad"\n',
            "[operator]",
            "reportOperatorIssue",
            id="order_mismatch",
        ),
        pytest.param(
            "from tests.prototypes.operators import Signal, contains\n"
            'contains(Signal([1, 2]), "bad")\n',
            "[misc]",
            "reportCallIssue",
            id="membership_mismatch",
        ),
        pytest.param(
            "from tests.prototypes.operators import Signal, all_of\nall_of(Signal(1))\n",
            "[arg-type]",
            "reportArgumentType",
            id="boolean_mismatch",
        ),
        pytest.param(
            "from typing import assert_type\n"
            "from tests.prototypes.operators import Signal\n"
            "assert_type(2 == Signal(1), Signal[bool])\n",
            "[assert-type]",
            "reportAssertTypeFailure",
            id="equality_is_identity",
        ),
        pytest.param(
            "from tests.prototypes.operators import Signal\nlen(Signal([1]))\n",
            "[arg-type]",
            "reportArgumentType",
            id="len_not_reactive",
        ),
        pytest.param(
            "from tests.prototypes.operators import Signal\n1 in Signal([1])\n",
            "[operator]",
            "reportOperatorIssue",
            id="in_not_reactive",
        ),
    ],
)
def test_operator_typing_contracts(
    tmp_path: Path,
    checker: str,
    source: str,
    mypy_code: str | None,
    pyright_code: str | None,
) -> None:
    fixture = tmp_path / "operators_contract.py"
    fixture.write_text(source)
    command = [sys.executable, "-m", checker]
    if checker == "mypy":
        command += ["--strict", "--show-error-codes"]
    command.append(str(fixture))
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout + result.stderr
    expected_code = mypy_code if checker == "mypy" else pyright_code
    if expected_code is None:
        assert result.returncode == 0, output
    else:
        assert result.returncode == 1, output
        assert expected_code in output, output
        assert str(fixture) in output, output
        assert " - error:" in output if checker == "pyright" else ": error:" in output
        # Pyright may emit both overload and argument diagnostics for one bad call.
        # Every negative must be rejected for its own contract, never an import failure.
        assert "reportMissingImports" not in output, output
        assert "[import-not-found]" not in output, output
