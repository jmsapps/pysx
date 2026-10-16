"""The platform matrix must keep predictions falsifiable and separate from evidence."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MATRIX = (
    ROOT / ".skills" / "tasks" / "PYSX-99-production-feedback" / "references" / "platform-matrix.md"
)
PLATFORMS = ("Linux", "Windows")
CONFIDENCE = {"high", "medium", "low"}


def rows() -> list[list[str]]:
    if not MATRIX.is_file():
        pytest.skip("the platform matrix is a local planning record")
    found: list[list[str]] = []

    for line in MATRIX.read_text(encoding="utf-8").splitlines():
        text = line.strip()

        if not text.startswith("|") or not text.endswith("|"):
            continue
        cells = [cell.strip() for cell in text.strip("|").split("|")]

        if len(cells) != 7 or not cells[0].isdigit():
            continue
        found.append(cells)

    return found


def test_platform_matrix_declares_rows() -> None:
    assert rows(), "the matrix declares no rows"


def test_platform_matrix_every_row_names_a_command() -> None:
    for cells in rows():
        command = cells[1]
        assert "`" in command, f"row {cells[0]} names no command"
        assert re.search(r"`[^`]+`", command), f"row {cells[0]} has an empty command"


def test_platform_matrix_every_row_states_a_macos_result() -> None:
    for cells in rows():
        result = cells[2]
        assert result.startswith(("observed", "pending")), (
            f"row {cells[0]} macOS cell is neither observed nor explicitly pending: {result!r}"
        )


def test_platform_matrix_unobserved_platforms_are_marked_predicted() -> None:
    """A prediction may never read as a pass."""

    for cells in rows():
        for name, value in zip(PLATFORMS, cells[3:5], strict=True):
            assert value.startswith(("predicted", "observed", "unsupported")), (
                f"row {cells[0]} {name} cell is unlabelled: {value!r}"
            )
            assert value != "pass", f"row {cells[0]} {name} claims a bare pass"


def test_platform_matrix_predictions_carry_confidence_and_a_mechanism() -> None:
    for cells in rows():
        predicted = [value for value in cells[3:5] if value.startswith("predicted")]

        if not predicted:
            continue
        assert cells[5].lower() in CONFIDENCE, f"row {cells[0]} has no confidence grade"
        assert len(cells[6]) > 40, f"row {cells[0]} gives no mechanism for its prediction"


def test_platform_matrix_records_its_host_and_provenance() -> None:
    text = MATRIX.read_text(encoding="utf-8") if MATRIX.is_file() else pytest.skip("local record")
    assert "Host of record:" in text
    assert "## Provenance" in text
    assert "not evidence" in text


def test_platform_matrix_covers_every_command_this_phase_runs() -> None:
    """Each qualified command needs a row, or a macOS pass would hide a platform gap."""
    text = MATRIX.read_text(encoding="utf-8") if MATRIX.is_file() else pytest.skip("local record")

    for command in (
        "tests/test_lsp.py",
        "editor test -- --suite client_verify",
        "editor test -- --suite editor_surface",
        "editor test -- --suite template_import_usage",
        "grammar -- --suite client_verify",
        "pysx.tooling",
        "build_vsix.py",
    ):
        assert command in text, f"no matrix row covers {command}"
