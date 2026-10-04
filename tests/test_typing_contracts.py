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
            'from pysx import signal\ncount = signal(1)\ncount.set("wrong")\n',
            "set", id="payload",
        ),
        pytest.param(
            'from pysx.wire import TextOp\nop: TextOp = {"op": "wrong", "id": "x", "v": "x"}\n',
            "Literal", id="discriminant",
        ),
        pytest.param(
            'from pysx.wire import TextOp\nop: TextOp = {"op": "text", "id": "x", "v": None}\n',
            "None", id="text-value",
        ),
    ],
)
def test_pysx_2_st_3_contracts(
    tmp_path: Path, checker: str, source: str, error: str | None,
) -> None:
    fixture = tmp_path / "contract.py"
    fixture.write_text(source)
    command = [sys.executable, "-m", checker]
    if checker == "mypy":
        command += ["--strict", "--show-error-codes"]
    command.append(str(fixture))
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
    )
    output = result.stdout + result.stderr
    if error is None:
        assert result.returncode == 0, output
    else:
        assert result.returncode == 1, output
        assert error in output, output
        assert str(fixture) in output, output
        line = 3 if error == "set" else 2
        assert f"{fixture}:{line}:" in output, output
        assert output.count(" - error:" if checker == "pyright" else ": error:") == 1, output
        expected = "[arg-type]" if error == "set" else "[typeddict-item]"
        if checker == "mypy":
            assert expected in output, output
        else:
            expected = "reportArgumentType" if error == "set" else "reportAssignmentType"
            assert expected in output, output
