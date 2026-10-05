"""Strict checker fixtures; intentional invalid programs remain text until execution."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
@pytest.mark.parametrize("valid", [True, False])
def test_events_immediate_types(tmp_path: Path, checker: str, valid: bool) -> None:
    source = "from pysx import BrowserEvent, native, on_event\n"

    if valid:
        source += "from typing import assert_type\n"
        source += "def handle(event: BrowserEvent) -> None:\n"
        source += "    assert_type(event.key, str)\n    assert_type(event.shift, bool)\n"
        source += "native.Input(on_keydown=on_event(handle, keys=('Enter',)))\n"
    else:
        source = "from pysx import on_event\n"
        source += "def handle(event: int) -> None:\n    pass\n"
        source += "on_event(handle)\non_event(lambda event: None, phase='wrong')\n"
    fixture = tmp_path / "event_contract.py"
    fixture.write_text(source)
    args = [sys.executable, "-m", checker]

    if checker == "mypy":
        args += ["--strict"]
    result = subprocess.run(
        [*args, str(fixture)], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False
    )
    output = result.stdout + result.stderr
    assert result.returncode == (0 if valid else 1), output

    if not valid:
        diagnostic = " - error:" if checker == "pyright" else ": error:"
        assert output.count(diagnostic) == 2, output


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
@pytest.mark.parametrize("valid", [True, False])
def test_bindings_form_types(tmp_path: Path, checker: str, valid: bool) -> None:
    source = "from pysx import native as n, signal\n"

    if valid:
        source += 'n.Input(bind_value=signal("text"))\n'
        source += 'n.Input(type="checkbox", bind_checked=signal(True))\n'
        source += 'n.Select(multiple=True, bind_selected=signal(["a"]))\n'
        source += 'n.Textarea(bind_value=signal({"text": ["a"]})["text"][0])\n'
    else:
        source += 'number = signal(1)\ntext = signal("bad")\n'
        source += "n.Input(bind_value=number)\n"
        source += "n.Input(bind_checked=text)\n"
        source += "n.Select(bind_selected=text)\n"
        source += "n.Textarea(bind_checked=signal(True))\n"
    fixture = tmp_path / "bindings_contract.py"
    fixture.write_text(source)
    command = [sys.executable, "-m", checker]

    if checker == "mypy":
        command += ["--strict", "--show-error-codes"]
    result = subprocess.run(
        [*command, str(fixture)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == (0 if valid else 1), output

    if not valid:
        diagnostic = " - error:" if checker == "pyright" else ": error:"
        assert output.count(diagnostic) == 4, output

        for name in ("bind_value", "bind_checked", "bind_selected"):
            assert name in output, output


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
@pytest.mark.parametrize("valid", [True, False])
def test_schema_native_types(tmp_path: Path, checker: str, valid: bool) -> None:
    from pysx.schema import BASELINE_TAGS, BOOLEAN_ATTRS, TAG_ATTRS, resolve_tag
    from scripts.generate_native import spelling

    source = "from pysx import native as n" + (", signal\n" if valid else "\n")

    if valid:
        for tag in dict.fromkeys(resolve_tag(tag) for tag in BASELINE_TAGS):
            attrs = TAG_ATTRS.get(tag, frozenset())
            kwargs = [
                f"{spelling(attr)}="
                + (
                    "(lambda event: None)"
                    if attr.startswith("on")
                    else "signal(True)"
                    if attr in BOOLEAN_ATTRS
                    else 'signal("2")'
                )
                for attr in sorted(attrs)
            ]
            source += f"n.{tag.capitalize()}(" + ", ".join(kwargs) + ")\n"
        source += 'n.Div(n.Span("hello"), custom_attrs={"aria-hidden": False})\n'
    else:
        source += 'n.Div(href="/bad")\nn.Input(checked="bad")\nn.A(href=1)\n'
        source += 'n.Input(on_click="bad")\n'

        for tag in BASELINE_TAGS:
            canonical = resolve_tag(tag)
            forbidden = "cols" if "href" in TAG_ATTRS.get(canonical, frozenset()) else "href"
            source += f'n.{canonical.capitalize()}({forbidden}="invalid")\n'
        source += 'n.Fragment(id="invalid")\n'
    fixture = tmp_path / "native_contract.py"
    fixture.write_text(source)
    command = [sys.executable, "-m", checker]

    if checker == "mypy":
        command += ["--strict", "--show-error-codes"]
    result = subprocess.run(
        [*command, str(fixture)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == (0 if valid else 1), output

    if not valid:
        diagnostic = " - error:" if checker == "pyright" else ": error:"
        assert output.count(diagnostic) == 5 + len(BASELINE_TAGS), output

        for name in ("href", "checked", "on_click"):
            assert name in output, output


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
            "from typing import assert_type\n"
            "from pysx import structured, list_index, dict_key, Projection, Structured\n"
            'root = structured({"rows": [1, 2]})\n'
            "assert_type(root, Structured[dict[str, list[int]]])\n"
            'rows = dict_key(root, "rows")\n'
            "assert_type(rows, Projection[list[int]])\n"
            "item = list_index(rows, 0)\n"
            "assert_type(item, Projection[int])\nitem.set(3)\n",
            None,
            id="structured_state_inference",
        ),
        pytest.param(
            "from pysx import structured, list_index\nitem = list_index(structured([1]), 0)\n"
            'item.set("bad")\n',
            "set",
            id="structured_state_payload",
        ),
        pytest.param(
            "from typing import assert_type\n"
            "from pysx import Signal, signal, derived\n"
            "from collections.abc import Callable\n"
            'n = signal(1)\ns = signal("a")\n'
            "out = derived(lambda: s() * n())\n"
            "assert_type(out, Signal[str])\n"
            "assert_type(out.subscribe(lambda value: value.upper()), Callable[[], None])\n",
            None,
            id="identity_settled_inference",
        ),
        pytest.param(
            "from pysx import signal\ncount = signal(1)\n"
            "count.subscribe(lambda value: value.upper())\n",
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
        expected = (
            "[attr-defined]"
            if error == "upper"
            else ("[arg-type]" if error == "set" else "[typeddict-item]")
        )

        if checker == "mypy":
            assert expected in output, output
        else:
            expected = (
                "reportAttributeAccessIssue"
                if error == "upper"
                else ("reportArgumentType" if error == "set" else "reportAssignmentType")
            )
            assert expected in output, output


UNIFIED_POSITIVE = """from typing import assert_type
from dataclasses import dataclass, replace
from pysx import Signal, derived
from tests.prototypes.unified import Unified, signal
root = signal({"rows": {"scores": [1, 2]}})
assert_type(root, Unified[dict[str, dict[str, list[int]]]])
item = root["rows"]["scores"][0]
assert_type(item, Unified[int])
item.set(3)
assert_type(signal(2) * 3, Signal[int])
assert_type(3 * signal(2), Signal[int])
assert_type(signal(2.0) * 3.0, Signal[float])
assert_type(3.0 * signal(2.0), Signal[float])
assert_type(derived(lambda: signal(2)() * 1.5), Signal[float])
@dataclass
class Person:
    name: str
person = signal(Person("Ada"))
name = person.project(lambda p: p.name, lambda p, name: replace(p, name=name))
assert_type(name, Unified[str])
name.set("Grace")
"""


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
@pytest.mark.parametrize(
    ("source", "valid"),
    [
        (UNIFIED_POSITIVE, True),
        ('from tests.prototypes.unified import signal\nsignal([1])[0].set("bad")\n', False),
        ('from tests.prototypes.unified import signal\nsignal({"a": 1})[2]\n', False),
        ('from tests.prototypes.unified import signal\nsignal([1])["a"]\n', False),
        ('from tests.prototypes.unified import signal\nsignal(1) * "bad"\n', False),
    ],
)
def test_unified_authoring_types(tmp_path: Path, checker: str, source: str, valid: bool) -> None:
    source = source.replace(
        "from tests.prototypes.unified import Unified, signal", "from pysx import Signal, signal"
    )
    source = source.replace(
        "from tests.prototypes.unified import signal", "from pysx import signal"
    )
    source = source.replace("Unified[", "Signal[")
    source = source.replace("from pysx import Signal, derived", "from pysx import derived")
    assert "tests.prototypes" not in source, (
        "prototype import survived the rewrite; this gate would type-check the "
        "prototype instead of the shipped package"
    )
    fixture = tmp_path / "unified_contract.py"
    fixture.write_text(source)
    command = [sys.executable, "-m", checker]

    if checker == "mypy":
        command += ["--strict", "--show-error-codes"]

    result = subprocess.run(
        [*command, str(fixture)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == (0 if valid else 1), output

    if not valid:
        assert str(fixture) in output, output
        assert "reportMissingImports" not in output, output
        assert "[import-not-found]" not in output, output


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
    "all_of(b, True)",
    "all_of(False, b)",
    "any_of(False, b)",
    "any_of(b, True)",
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
def test_python_operator_operators_reactive_typing_contracts(
    tmp_path: Path,
    checker: str,
    source: str,
    mypy_code: str | None,
    pyright_code: str | None,
) -> None:
    source = source.replace("from tests.prototypes.operators import", "from pysx import")
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
