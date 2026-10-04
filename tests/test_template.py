
from typing import TYPE_CHECKING

from pysx.template import dedent_fragments

if TYPE_CHECKING:
    from string.templatelib import Template


def _counter_template(count: object, handler: object) -> Template:
    return t"""
        Page(id="container"):
            "Count: "; {count}
            Action(type="button", onClick={handler}):
                "Increment"
    """


def test_golden_counter() -> None:
    tpl = _counter_template("C", "H")
    got = dedent_fragments(tpl.strings)
    assert got == (
        '\nPage(id="container"):\n    "Count: "; ',
        '\n    Action(type="button", onClick=',
        '):\n        "Increment"\n',
    ), got


def test_structure_preserved() -> None:
    tpl = _counter_template("C", "H")
    got = dedent_fragments(tpl.strings)
    assert len(got) == len(tpl.strings)
    for a, b in zip(tpl.strings, got, strict=True):
        assert a.count("\n") == b.count("\n"), (a, b)


def test_nul_sentinel_survives() -> None:
    """Proves no in-band sentinel is used for the join/split."""
    marker = "\x00\x00"
    tpl = t"""
        div:
            p: {marker}X\x00\x00Y
    """
    got = dedent_fragments(tpl.strings)
    assert "\x00\x00" in "".join(got), got
    assert got == ("\ndiv:\n    p: ", "X\x00\x00Y\n"), got


def test_doubled_braces() -> None:
    tpl = t"""
        div(style="{{a:1}}"):
            p: {1}
    """
    got = dedent_fragments(tpl.strings)
    assert got[0] == '\ndiv(style="{a:1}"):\n    p: ', repr(got[0])


def test_content_on_quote_line_is_deterministic() -> None:
    """Text on the t\"\"\" line has no knowable indent, so it is excluded from the
    margin and left in place. Nesting under it cannot be recovered here; the
    parser rejects this shape instead."""
    tpl = t"""div:
        p: {1}
        q: 2
    """
    got = dedent_fragments(tpl.strings)
    assert got[0] == "div:\np: ", repr(got[0])
    assert got[1] == "\nq: 2\n", repr(got[1])


def test_blank_lines_normalized_not_counted() -> None:
    tpl = t"""
        a:

            b: {1}
    """
    got = dedent_fragments(tpl.strings)
    assert got[0] == "\na:\n\n    b: ", repr(got[0])


def test_hole_alone_on_a_line_keeps_its_indentation() -> None:
    """A line whose only text is indentation, because a hole follows, must not
    be treated as blank — its indent decides the node's parent."""
    tpl = t"""
        ul:
            {1}
        p: "after"
    """
    got = dedent_fragments(tpl.strings)
    assert got[0] == "\nul:\n    ", repr(got[0])
    assert got[1] == '\np: "after"\n', repr(got[1])


def test_no_margin_is_identity() -> None:
    tpl = t"""a
b {1}"""
    assert dedent_fragments(tpl.strings) is tpl.strings


def test_tabs_not_mixed_with_spaces() -> None:
    tpl = t"""
\t\tdiv:
\t\t\tp: {1}
"""
    got = dedent_fragments(tpl.strings)
    assert got[0] == "\ndiv:\n\tp: ", repr(got[0])
