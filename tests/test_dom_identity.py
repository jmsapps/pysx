"""Canonical renderer paths and legal range boundaries."""

import re

import pytest

from pysx import Fragment, each, pysx, signal
from pysx.identity import OwnerPath, key_segment
from pysx.render import render
from pysx.render_tree import diff, snapshot


@pytest.mark.parametrize("key", ["", "a:b", 'quote"{λ}-->', "~6162", "a", "ab", "😀"])
def test_stable_paths_key_encoding(key: str) -> None:
    segment = key_segment(key)
    assert ":" not in segment
    assert '"' not in segment
    assert "--" not in segment
    assert segment != key_segment(key + "x")
    markup = OwnerPath("0").row(key).wrap("", "row")
    assert markup.count("<!--") == 2
    assert markup.count("-->") == 2


def test_stable_paths_multi_root_ranges_and_nested_handlers() -> None:
    rows = signal(["a:b", "a", '"-->{λ}'])

    def row(value: str) -> Fragment:
        def click(_event: object) -> str:
            return value

        return pysx(t"""
            span(title=">", onClick={click}): {value}
            input(value={value})
        """)

    def view() -> Fragment:
        return pysx(t"""div: {each(rows, row, key=lambda value: value)}""")

    rendered = render(view)
    assert "pysx-list" not in rendered.body
    assert len(re.findall(r"<!--pysx:row:[a-f0-9]+:start-->", rendered.body)) == 3
    assert len(rendered.handlers) == 3
    assert 'title=">"' not in rendered.body
    assert 'title="&gt;"' in rendered.body
    assert all("~" in handler or ":a:" in handler for handler in rendered.handlers)
    before = set(rendered.handlers)
    rows.set(list(reversed(rows())))
    op = rendered.watchers[0].refresh()[0]
    assert op["op"] == "list"
    assert op["html"] == {}
    assert set(rendered.handlers) == before
    rendered.dispose()


def test_stable_paths_duplicate_keys_rejected_before_render() -> None:
    calls: list[object] = []

    def row(value: str | int) -> Fragment:
        calls.append(value)

        return pysx(t"""span: {value}""")

    values: list[str | int] = [1, "1"]
    with pytest.raises(ValueError, match="duplicate list key"):
        render(lambda: pysx(t"""{each(values, row, key=lambda value: value)}"""))
    assert calls == []


def test_stable_paths_constrained_slots_use_comments() -> None:
    value = signal("text")
    rendered = render(
        lambda: pysx(t"""
        table:
          tbody:
            tr:
              td: {value}
        select:
          option: {value}
        svg:
          text: {value}
        math:
          mi: {value}
    """)
    )
    assert "pysx-slot" not in rendered.body
    assert len(rendered.watchers) == 4
    value.set("changed")
    assert all(watcher.refresh()[0]["op"] == "text" for watcher in rendered.watchers)
    rendered.dispose()


def test_identity_cleanup_encoded_keys_have_disjoint_owner_paths() -> None:
    keys = ["a", "a:b", "a:b:c", "", "~", "~61", "λ", '"-->{}']
    paths = [OwnerPath("0").row(key).value for key in keys]
    assert len(set(paths)) == len(keys)
    assert all(not left.startswith(right) for left in paths for right in paths if left != right)


def test_identity_cleanup_foreign_snapshot_preserves_attribute_case() -> None:
    before = snapshot('<svg data-pysx-el="shape" viewBox="0 0 10 10"><g/></svg>')
    after = snapshot('<svg data-pysx-el="shape" viewBox="0 0 20 20"><g/></svg>')
    assert 'viewBox="0 0 20 20"' in after.markup()
    assert diff(before, after, "row") == [
        {"op": "attr", "id": "shape", "name": "viewBox", "v": "0 0 20 20"}
    ]
