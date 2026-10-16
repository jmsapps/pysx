"""Structural markup features: semantic tokens, folding, symbols and selection ranges."""

from __future__ import annotations

import sys
from itertools import pairwise
from typing import TYPE_CHECKING, Any, cast

import pytest
from test_lsp import Client

from pysx.lsp_features import (
    SEMANTIC_TOKEN_MODIFIERS,
    SEMANTIC_TOKEN_TYPES,
    semantic_tokens,
    template_calls,
)
from pysx.lsp_workspace import position

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

SOURCE = '''from pysx import pysx


def view(items, flag, mode):
    return pysx(
        t"""
        div(class="wrap"):
            if {flag}:
                span(id="yes"): "ok"
            elif {mode}:
                span: "maybe"
            else:
                span: "no"
            match {mode}:
                case "a":
                    b: "first"
                case _:
                    i: "rest"
            for item in {items}:
                let label = {item}
                li(class="row"):
                    {label}
    """,
    )
'''


@pytest.fixture
def client(tmp_path: Path) -> Iterator[Client]:
    instance = Client(tmp_path)

    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def opened(tmp_path: Path, client: Client) -> tuple[Client, Path]:
    path = tmp_path / "view.py"
    client.open(path, SOURCE)

    return client, path


def document(path: Path) -> dict[str, object]:
    return {"textDocument": {"uri": path.as_uri()}}


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_shared_template_tokens_are_unique_in_collector_and_wire(
    tmp_path: Path, encoding: str
) -> None:
    path = tmp_path / "shared.py"
    source = (
        "from pysx import pysx, pysx as render\n"
        'template = t\'"🌍"; div(title="🌟")\'\n'
        "first = pysx(template)\n"
        "second = render(template)\n"
        "distinct = pysx(t'div(title=\"🌟\")')\n"
    )
    collected = semantic_tokens(template_calls(source, str(path), tmp_path, {str(path): source}))
    assert len(collected) == len(set(collected))
    assert sum(source[span.start : span.end] == "div" for span, _, _ in collected) == 2
    instance = Client(tmp_path, encoding)

    try:
        instance.open(path, source)
        wire = tokens_of(instance, path)
        assert len(wire) == len(set(wire))
        codec, width = {
            "utf-8": ("utf-8", 1),
            "utf-16": ("utf-16-le", 2),
            "utf-32": ("utf-32-le", 4),
        }[encoding]
        words = [
            source.splitlines()[line]
            .encode(codec)[start * width : (start + length) * width]
            .decode(codec)
            for line, start, length, _, _ in wire
        ]
        assert words.count("div") == 2
        assert words.count("title") == 2
        assert words.count('"🌟"') == 2

        for previous, current in pairwise(wire):
            assert (previous[0], previous[1] + previous[2]) <= (current[0], current[1])
    finally:
        instance.close()


def decoded(data: list[int]) -> list[tuple[int, int, int, str, tuple[str, ...]]]:
    out: list[tuple[int, int, int, str, tuple[str, ...]]] = []
    line = 0
    character = 0

    for index in range(0, len(data), 5):
        delta_line, delta_start, length, kind, modifiers = data[index : index + 5]
        line += delta_line
        character = delta_start if delta_line else character + delta_start
        out.append(
            (
                line,
                character,
                length,
                SEMANTIC_TOKEN_TYPES[kind],
                tuple(
                    name
                    for bit, name in enumerate(SEMANTIC_TOKEN_MODIFIERS)
                    if modifiers & (1 << bit)
                ),
            )
        )

    return out


def text_at(entry: tuple[int, int, int, str, tuple[str, ...]]) -> str:
    line, character, length, _kind, _modifiers = entry

    return SOURCE.splitlines()[line][character : character + length]


def tokens_of(client: Client, path: Path) -> list[tuple[int, int, int, str, tuple[str, ...]]]:
    result = client.request("textDocument/semanticTokens/full", document(path))
    assert isinstance(result, dict)

    return decoded(cast("list[int]", result["data"]))


def test_highlighting_cooperation_preserves_standalone_tokens(tmp_path: Path) -> None:
    instance = Client(tmp_path)

    try:
        assert "semanticTokensProvider" in instance.initialization["capabilities"]
        path = tmp_path / "view.py"
        instance.open(path, SOURCE)
        assert tokens_of(instance, path)
    finally:
        instance.close()


def test_buffer_readiness_structure_does_not_wait_for_any_model(tmp_path: Path) -> None:
    release = tmp_path / "release"
    command = [
        sys.executable,
        "-c",
        f"""
import time
from pathlib import Path
from pysx.lsp_workspace import WorkspaceIndex
from pysx.lsp_server import create_server
original = WorkspaceIndex.build
def blocked(self, *args, **kwargs):
    while not Path({str(release)!r}).exists():
        time.sleep(0.01)
    return original(self, *args, **kwargs)
WorkspaceIndex.build = blocked
create_server().start_io()
""",
    ]
    instance = Client(tmp_path, command=command)
    path = tmp_path / "view.py"

    try:
        instance.open(path, SOURCE)
        assert tokens_of(instance, path)
        assert instance.request("textDocument/documentSymbol", document(path))
        assert instance.status()["workspaces"][0]["ready"] is False
        instance.change(path, SOURCE.replace('"wrap"', '"changed"'), 2)
        result = instance.request("pysx/markupTokens", document(path))
        assert isinstance(result, dict)
        assert result["version"] == 2
        assert tokens_of(instance, path)
        pending = instance.send(
            "textDocument/hover",
            {**document(path), "position": {"line": 6, "character": 8}},
            request=True,
        )
        instance.send("$/cancelRequest", {"id": pending})
        cancelled = instance.wait(lambda message: message.get("id") == pending)
        assert cancelled["error"]["code"] == -32800
        assert tokens_of(instance, path)
    finally:
        release.touch()
        instance.close()


def test_highlighting_cooperation_decorates_only_uncovered_markup(tmp_path: Path) -> None:
    instance = Client(tmp_path, options={"markupHighlighting": "decorations"})
    source = (
        "from pysx import pysx, pysx as render\n"
        "value = 'hello'\n"
        "literal = pysx(t'div: {value}')\n"
        "template = t'span(class=\"row\"): {value}'\n"
        "shared = pysx(template)\n"
        "aliased = render(t'b: {value}')\n"
    )

    try:
        assert "semanticTokensProvider" not in instance.initialization["capabilities"]
        path = tmp_path / "view.py"
        instance.open(path, source)
        result = cast("dict[str, Any]", instance.request("pysx/markupTokens", document(path)))
        assert result["version"] == 1
        assert result["generation"] > 0
        spans: set[str] = set()

        for token in cast("list[dict[str, Any]]", result["tokens"]):
            target = cast("dict[str, dict[str, int]]", token["range"])
            line = source.splitlines()[target["start"]["line"]]
            spans.add(line[target["start"]["character"] : target["end"]["character"]])
        assert {"span", "class", '"row"', "b"} <= spans
        assert "div" not in spans
        assert "value" not in spans
        instance.change(path, source.replace("span(", "p("), 2)
        changed = cast("dict[str, Any]", instance.request("pysx/markupTokens", document(path)))
        assert changed["version"] == 2
        assert changed["generation"] > result["generation"]
    finally:
        instance.close()


def test_edits_formatting_semantic_tokens_cover_every_grammar_form(
    opened: tuple[Client, Path],
) -> None:
    client, path = opened
    found = {(text_at(entry), entry[3], entry[4]) for entry in tokens_of(client, path)}
    assert ("div", "type", ("defaultLibrary",)) in found
    assert ("class", "property", ()) in found
    assert ('"wrap"', "string", ()) in found
    assert {("if", "keyword", ()), ("elif", "keyword", ()), ("else", "keyword", ())} <= found
    assert {("match", "keyword", ()), ("case", "keyword", ())} <= found
    assert {("for", "keyword", ()), ("let", "keyword", ())} <= found
    assert {("item", "variable", ()), ("label", "variable", ())} <= found


def test_edits_formatting_semantic_tokens_claim_no_python_region(
    opened: tuple[Client, Path],
) -> None:
    client, path = opened
    lines = SOURCE.splitlines()
    template_lines = {
        number
        for number, text in enumerate(lines)
        if number > lines.index('        t"""') and number < len(lines) - 2
    }

    for entry in tokens_of(client, path):
        line, character, length, _kind, _modifiers = entry
        assert line in template_lines, f"token on Python line {line}"
        span = lines[line][character : character + length]
        assert span.strip() == span
        assert "{" not in span
        assert "}" not in span


def test_edits_formatting_semantic_tokens_are_ordered_and_non_overlapping(
    opened: tuple[Client, Path],
) -> None:
    client, path = opened
    previous = (-1, -1)

    for line, character, length, _kind, _modifiers in tokens_of(client, path):
        assert (line, character) > previous
        previous = (line, character + length - 1)


def test_edits_formatting_folding_covers_nested_constructs(
    opened: tuple[Client, Path],
) -> None:
    client, path = opened
    result = client.request("textDocument/foldingRange", document(path))
    assert isinstance(result, list)
    ranges = sorted(
        (int(item["startLine"]), int(item["endLine"]))
        for item in cast("list[dict[str, Any]]", result)
    )
    assert ranges, "expected folding ranges"
    lines = SOURCE.splitlines()
    outer = next(start for start, _ in ranges if lines[start].strip().startswith("div("))
    assert any(start > outer and lines[start].strip().startswith("if ") for start, _ in ranges)
    assert all(end > start for start, end in ranges)
    assert all(
        (other_start >= start and other_end <= end) or other_start > end
        for start, end in ranges
        for other_start, other_end in ranges
        if (other_start, other_end) != (start, end) and other_start >= start
    )


def test_edits_formatting_document_symbols_nest_with_contained_selection(
    opened: tuple[Client, Path],
) -> None:
    client, path = opened
    result = client.request("textDocument/documentSymbol", document(path))
    assert isinstance(result, list)

    def check(items: list[dict[str, Any]]) -> list[str]:
        names: list[str] = []

        for item in items:
            whole, selection = item["range"], item["selectionRange"]
            assert (selection["start"]["line"], selection["start"]["character"]) >= (
                whole["start"]["line"],
                whole["start"]["character"],
            )
            assert (selection["end"]["line"], selection["end"]["character"]) <= (
                whole["end"]["line"],
                whole["end"]["character"],
            )
            names.append(item["name"])
            names.extend(check(item.get("children") or []))

        return names

    names = check(cast("list[dict[str, Any]]", result))
    assert names[0] == "div"
    assert {"if", "elif", "else", "match", "case", "for", "let", "span", "li"} <= set(names)


def test_edits_formatting_selection_ranges_widen_to_the_call(
    opened: tuple[Client, Path],
) -> None:
    client, path = opened
    at = SOURCE.index('id="yes"')
    point = position(SOURCE, at)
    result = client.request(
        "textDocument/selectionRange",
        {
            **document(path),
            "positions": [{"line": point.line, "character": point.character}],
        },
    )
    assert isinstance(result, list)
    chain: list[dict[str, Any]] = []
    current: dict[str, Any] | None = cast("dict[str, Any]", result[0])

    while current is not None:
        chain.append(current["range"])
        current = current.get("parent")

    assert len(chain) >= 4

    for inner, outer in pairwise(chain):
        assert (outer["start"]["line"], outer["start"]["character"]) <= (
            inner["start"]["line"],
            inner["start"]["character"],
        )
        assert (outer["end"]["line"], outer["end"]["character"]) >= (
            inner["end"]["line"],
            inner["end"]["character"],
        )
    assert chain[0]["start"]["line"] == point.line
    assert chain[-1]["start"]["line"] <= SOURCE.splitlines().index("    return pysx(")


def test_edits_formatting_structural_features_ignore_unparseable_markup(
    tmp_path: Path, client: Client
) -> None:
    path = tmp_path / "broken.py"
    source = "from pysx import pysx\nview = pysx(t'''\n    div(class=\n'''"
    client.open(path, source)
    assert tokens_of(client, path) == []
    assert client.request("textDocument/foldingRange", document(path)) == []
    assert client.request("textDocument/documentSymbol", document(path)) == []


def test_editor_surface_tokens_cover_native_styled_and_callable_tags(
    tmp_path: Path, client: Client
) -> None:
    """Every tag flavour the grammar supports must be classified, not just natives."""
    path = tmp_path / "view.py"
    source = (
        "from pysx import Fragment, pysx, styled\n"
        "from pysx.native import div\n"
        "\n"
        "\n"
        "Box = styled(div)(t'padding: 4px')\n"
        "\n"
        "\n"
        "def Card(*, title: str) -> Fragment:\n"
        "    return pysx(t'p: {title}')\n"
        "\n"
        "\n"
        "view = pysx(t'''\n"
        '    section(id="root"):\n'
        "        Box:\n"
        '            Card(title="x")\n'
        "''')\n"
    )
    client.open(path, source)
    result = client.request("textDocument/semanticTokens/full", document(path))
    assert isinstance(result, dict)
    lines = source.splitlines()
    found = {
        (lines[line][character : character + length], kind)
        for line, character, length, kind, _modifiers in decoded(cast("list[int]", result["data"]))
    }
    assert ("section", "type") in found
    assert ("Box", "class") in found
    assert ("Card", "class") in found
    assert ("id", "property") in found
    assert ("title", "property") in found


def test_editor_surface_structural_features_answer_for_every_open_buffer(
    tmp_path: Path, client: Client
) -> None:
    first, second = tmp_path / "one.py", tmp_path / "two.py"
    client.open(first, SOURCE)
    client.open(second, SOURCE.replace("wrap", "other"))

    for path in (first, second):
        assert client.request("textDocument/documentSymbol", document(path))
        assert client.request("textDocument/foldingRange", document(path))
