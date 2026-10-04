"""Executable isolated architecture proofs; these are not production feature tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from prototypes.host_cases import CASES as HOST_CASES
from prototypes.operator_cases import CASES as OPERATOR_CASES
from prototypes.template_cases import CASES as TEMPLATE_CASES

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

assert not OPERATOR_CASES.keys() & TEMPLATE_CASES.keys(), "duplicate architecture proof identity"
AUTHORING_CASES = {**TEMPLATE_CASES, **OPERATOR_CASES}
assert AUTHORING_CASES, "empty architecture proof registry"


@pytest.mark.parametrize("proof", AUTHORING_CASES.values(), ids=AUTHORING_CASES.keys())
def test_authoring_contract(tmp_path: Path, proof: Callable[[Path], None]) -> None:
    proof(tmp_path)


assert HOST_CASES, "empty host/state proof registry"


@pytest.mark.parametrize("proof", HOST_CASES.values(), ids=HOST_CASES.keys())
def test_hosting_contract(tmp_path: Path, proof: Callable[[Path], None]) -> None:
    proof(tmp_path)
