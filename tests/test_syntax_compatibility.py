"""Frozen trees and coordinates from the grammar before shorthand invocations."""

import dataclasses
import json
from enum import Enum
from pathlib import Path
from typing import cast

import pytest

from pysx.parser import (
    Case,
    Conditional,
    Element,
    Hole,
    LiteralText,
    Local,
    Loop,
    Match,
    Position,
    PysxSyntaxError,
    Skeleton,
    Span,
    parse,
)

FIXTURE = Path(__file__).parent / "fixtures" / "template_syntax_compatibility.json"


def encode_syntax(value: object) -> object:
    if isinstance(value, LiteralText):

        return {"literal": str(value), "span": encode_syntax(value.span)}

    if isinstance(
        value, (Skeleton, Element, Conditional, Loop, Local, Match, Case, Hole, Position, Span)
    ):

        return {
            "type": type(value).__name__,
            **{
                field.name: encode_syntax(cast("object", getattr(value, field.name)))
                for field in dataclasses.fields(value)
            },
        }

    if isinstance(value, Enum):

        return value.value

    if isinstance(value, (tuple, list)):

        return [encode_syntax(item) for item in cast("tuple[object, ...]", value)]

    if value is None or isinstance(value, (str, int, float, bool)):

        return value

    raise AssertionError(f"unrecognized syntax value: {type(value).__name__}")


def _cases() -> list[dict[str, object]]:
    cases = cast("list[dict[str, object]]", json.loads(FIXTURE.read_text()))
    assert len(cases) >= 742
    assert sum(case["accepted"] is True for case in cases) >= 726

    return cases


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["name"]))
def test_frozen_template_syntax(case: dict[str, object]) -> None:
    fragments = tuple(cast("list[str]", case["fragments"]))

    if case["accepted"]:
        assert encode_syntax(parse(fragments)) == case["skeleton"]

        return

    # Only these additions may change an old rejection into an acceptance.
    if "addition" in case:

        return

    with pytest.raises(PysxSyntaxError) as caught:
        parse(fragments)
    assert str(caught.value) == case["error"]
    assert encode_syntax(caught.value.position) == case["position"]


def test_compatibility_addition_ledger() -> None:
    assert {case["addition"] for case in _cases() if "addition" in case} == {
        "bare-name",
        "bare-attributes",
        "inline-sibling",
        "parenthesized-trailing-separator",
    }
