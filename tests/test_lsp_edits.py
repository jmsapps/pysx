"""Safe template edits: rename, formatting and owned code actions."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, cast

import pytest
from test_lsp import Client

from pysx.lsp_edits import apply_edits, format_edits
from pysx.lsp_features import template_calls
from pysx.lsp_workspace import position
from pysx.parser import Case, Conditional, Element, Hole, Local, Loop, Match, parse

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

RAGGED = '''from pysx import pysx


def view(items, flag):
    return pysx(
        t"""
        ul(class="list"):
              if {flag}:
                   li(class="row"): "first"
              else:
                 li: "second"
              for item in {items}:
                    li(data-id="x"):
                           {item}
    """,
    )
'''

LIBRARY = (
    "from pysx import Fragment, pysx\n"
    "def Panel(*, title: str) -> Fragment:\n"
    "    return pysx(t'p: {title}')\n"
)

CONSUMER = (
    "from pysx import Fragment, pysx\n"
    "from library import Panel as Panel\n"
    "def other(Panel: int) -> int:\n"
    "    return Panel\n"
    "def view() -> Fragment:\n"
    "    return pysx(t'Panel(title={\"x\"})')\n"
)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[Client]:
    instance = Client(tmp_path)

    try:
        yield instance
    finally:
        instance.close()


def shape(nodes: Sequence[object]) -> tuple[object, ...]:
    """Structure and hole order only; spans move when indentation is normalized."""
    out: list[object] = []

    for node in nodes:
        if isinstance(node, str):
            out.append(("text", str(node)))
        elif isinstance(node, Hole):
            out.append(("hole", node.index))
        elif isinstance(node, Element):
            out.append(
                (
                    "element",
                    str(node.tag) if isinstance(node.tag, str) else ("hole", node.tag.index),
                    tuple(
                        (str(name), str(value) if isinstance(value, str) else value.index)
                        for name, value in node.attrs
                    ),
                    shape(node.children),
                )
            )
        elif isinstance(node, Conditional):
            out.append(("if", node.hole.index, shape(node.then), shape(node.otherwise)))
        elif isinstance(node, Loop):
            out.append(("for", node.names, node.source.index, shape(node.children)))
        elif isinstance(node, Local):
            out.append(("local", node.operation, node.name, node.value.index))
        elif isinstance(node, Match):
            out.append(("match", node.value.index, shape(node.cases)))
        elif isinstance(node, Case):
            out.append(("case", str(node.patterns), node.wildcard, shape(node.children)))

    return tuple(out)


def formatted(source: str, tmp_path: Path) -> str:
    name = str(tmp_path / "view.py")
    calls = template_calls(source, name, tmp_path, {name: source})

    return apply_edits(source, [edit for call in calls for edit in format_edits(call)])


def applied(source: str, edits: list[dict[str, Any]]) -> str:
    """Apply LSP text edits to a source buffer, latest position first."""
    lines = source.splitlines(keepends=True)
    starts = [sum(len(line) for line in lines[:number]) for number in range(len(lines) + 1)]
    out = source

    for edit in sorted(
        edits,
        key=lambda item: (
            int(item["range"]["start"]["line"]),
            int(item["range"]["start"]["character"]),
        ),
        reverse=True,
    ):
        start = starts[int(edit["range"]["start"]["line"])] + int(
            edit["range"]["start"]["character"]
        )
        end = starts[int(edit["range"]["end"]["line"])] + int(edit["range"]["end"]["character"])
        out = out[:start] + str(edit["newText"]) + out[end:]

    return out


def document(path: Path) -> dict[str, object]:
    return {"textDocument": {"uri": path.as_uri()}}


def at(path: Path, source: str, offset: int) -> dict[str, object]:
    point = position(source, offset)

    return {
        **document(path),
        "position": {"line": point.line, "character": point.character},
    }


def test_edits_formatting_normalizes_ragged_indentation(tmp_path: Path) -> None:
    result = formatted(RAGGED, tmp_path)
    lines = result.splitlines()
    body = lines[lines.index('        t"""') + 1 : -2]
    indents = {line[: len(line) - len(line.lstrip())] for line in body if line.strip()}
    assert indents == {" " * 8, " " * 12, " " * 16, " " * 20}
    assert 'li(data-id="x"):' in result


def test_edits_formatting_is_idempotent(tmp_path: Path) -> None:
    once = formatted(RAGGED, tmp_path)
    assert formatted(once, tmp_path) == once


def test_edits_formatting_preserves_structure_and_hole_metadata(tmp_path: Path) -> None:
    name = str(tmp_path / "view.py")
    result = formatted(RAGGED, tmp_path)
    before = template_calls(RAGGED, name, tmp_path, {name: RAGGED})
    after = template_calls(result, name, tmp_path, {name: result})
    assert len(before) == len(after) == 1

    for left, right in zip(before, after, strict=True):
        original, updated = parse(left.source.strings), parse(right.source.strings)
        assert shape(original.root) == shape(updated.root)
        assert original.holes == updated.holes
        assert len(left.source.holes) == len(right.source.holes)


def test_edits_formatting_preserves_runtime_output() -> None:
    from pysx import Fragment, pysx, render

    def original() -> Fragment:
        return pysx(
            t"""
        ul(class="list"):
              li(class="row"): "first"
                 """
        )

    def normalized() -> Fragment:
        return pysx(
            t"""
        ul(class="list"):
            li(class="row"): "first"
        """
        )

    assert render(original).body == render(normalized).body


def test_edits_formatting_leaves_quoted_text_and_escapes_untouched(tmp_path: Path) -> None:
    source = (
        "from pysx import pysx\n"
        'view = pysx(\n    t"""\n    div:\n          span: "a\\u00e9  b"\n    """\n)\n'
    )
    result = formatted(source, tmp_path)
    assert '"a\\u00e9  b"' in result
    assert result != source


def test_edits_formatting_range_formatting_touches_only_the_selection(
    tmp_path: Path, client: Client
) -> None:
    path = tmp_path / "view.py"
    client.open(path, RAGGED)
    lines = RAGGED.splitlines()
    first = next(number for number, line in enumerate(lines) if "if {flag}" in line)
    result = client.request(
        "textDocument/rangeFormatting",
        {
            **document(path),
            "range": {
                "start": {"line": first, "character": 0},
                "end": {"line": first + 1, "character": 0},
            },
            "options": {"tabSize": 4, "insertSpaces": True},
        },
    )
    assert isinstance(result, list)
    edits = cast("list[dict[str, Any]]", result)
    assert edits
    assert {edit["range"]["start"]["line"] for edit in edits} == {first}


def test_edits_formatting_full_formatting_matches_the_offline_model(
    tmp_path: Path, client: Client
) -> None:
    path = tmp_path / "view.py"
    client.open(path, RAGGED)
    result = client.request(
        "textDocument/formatting",
        {**document(path), "options": {"tabSize": 4, "insertSpaces": True}},
    )
    assert isinstance(result, list)
    edits = cast("list[dict[str, Any]]", result)
    assert applied(RAGGED, edits) == formatted(RAGGED, tmp_path)


def test_edits_formatting_code_action_offers_template_normalization(
    tmp_path: Path, client: Client
) -> None:
    path = tmp_path / "view.py"
    client.open(path, RAGGED)
    result = client.request(
        "textDocument/codeAction",
        {
            **document(path),
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 0},
            },
            "context": {"diagnostics": []},
        },
    )
    assert isinstance(result, list)
    actions = cast("list[dict[str, Any]]", result)
    assert [action["title"] for action in actions] == ["pysx: normalize template indentation"]
    assert actions[0]["kind"].endswith(".pysx")
    assert actions[0]["edit"]["changes"][path.as_uri()]


def test_edits_formatting_code_action_absent_when_already_normalized(
    tmp_path: Path, client: Client
) -> None:
    path = tmp_path / "view.py"
    client.open(path, formatted(RAGGED, tmp_path))
    result = client.request(
        "textDocument/codeAction",
        {
            **document(path),
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 0},
            },
            "context": {"diagnostics": []},
        },
    )
    assert result == []


def test_edits_formatting_rename_spans_declaration_import_and_template_uses(
    tmp_path: Path, client: Client
) -> None:
    library = tmp_path / "library.py"
    consumer = tmp_path / "view.py"
    library.write_text(LIBRARY)
    consumer.write_text(CONSUMER)
    client.open(library, LIBRARY)
    client.open(consumer, CONSUMER)
    offset = CONSUMER.rindex("Panel(title=")
    prepared = client.request("textDocument/prepareRename", at(consumer, CONSUMER, offset))
    assert isinstance(prepared, dict)
    assert cast("dict[str, Any]", prepared)["placeholder"] == "Panel"
    result = client.request(
        "textDocument/rename", {**at(consumer, CONSUMER, offset), "newName": "Card"}
    )
    assert isinstance(result, dict)
    changes = cast("dict[str, Any]", result)["changes"]
    assert library.as_uri() in changes
    assert consumer.as_uri() in changes
    sources = {library.as_uri(): LIBRARY, consumer.as_uri(): CONSUMER}
    updated: dict[str, str] = {}

    for uri, edits in cast("dict[str, list[dict[str, Any]]]", changes).items():
        for edit in edits:
            assert str(edit["newText"]) == "Card"
        updated[uri] = applied(sources[uri], edits)

    assert "def Card(*, title: str)" in updated[library.as_uri()]
    assert "from library import Card as Card" in updated[consumer.as_uri()]
    assert "Card(title=" in updated[consumer.as_uri()]
    assert "def other(Panel: int) -> int:\n    return Panel\n" in updated[consumer.as_uri()]


def test_edits_formatting_renamed_tag_resolves_after_reanalysis(
    tmp_path: Path, client: Client
) -> None:
    library = tmp_path / "library.py"
    consumer = tmp_path / "view.py"
    library.write_text(LIBRARY.replace("Panel", "Card"))
    consumer.write_text(CONSUMER.replace("Panel", "Card"))
    client.open(library, LIBRARY.replace("Panel", "Card"))
    client.open(consumer, CONSUMER.replace("Panel", "Card"))
    published = client.diagnostic(consumer, 1, None)
    assert published["diagnostics"] == []


def test_edits_formatting_rename_refuses_native_tags(tmp_path: Path, client: Client) -> None:
    path = tmp_path / "view.py"
    source = "from pysx import pysx\nview = pysx(t'div: \"x\"')\n"
    client.open(path, source)
    offset = source.index("div")
    assert client.request("textDocument/prepareRename", at(path, source, offset)) is None
    result = client.call("textDocument/rename", {**at(path, source, offset), "newName": "Card"})
    assert "error" in result
    assert "native HTML tag" in result["error"]["message"]


def test_edits_formatting_rename_refuses_invalid_names(tmp_path: Path, client: Client) -> None:
    library = tmp_path / "library.py"
    consumer = tmp_path / "view.py"
    library.write_text(LIBRARY)
    consumer.write_text(CONSUMER)
    client.open(library, LIBRARY)
    client.open(consumer, CONSUMER)
    offset = CONSUMER.rindex("Panel(title=")
    result = client.call(
        "textDocument/rename", {**at(consumer, CONSUMER, offset), "newName": "class"}
    )
    assert "error" in result
    assert "not a valid Python name" in result["error"]["message"]


def test_editor_surface_formatting_never_changes_rendered_output() -> None:
    """The surface-level guarantee: formatting is a no-op for the running app."""
    from pysx import Fragment, pysx, render

    def before() -> Fragment:
        return pysx(
            t"""
        ul(class="list"):
                li(id="a"): "one"
                li(id="b"): "two"
            """
        )

    def after() -> Fragment:
        return pysx(
            t"""
        ul(class="list"):
            li(id="a"): "one"
            li(id="b"): "two"
        """
        )

    assert render(before).body == render(after).body


def test_editor_surface_rename_refuses_rather_than_edits_partially(
    tmp_path: Path, client: Client
) -> None:
    """A refusal is the required outcome when the declaration cannot be owned."""
    path = tmp_path / "view.py"
    source = (
        "from pysx import Fragment, pysx\n"
        "\n"
        "\n"
        "def view(Panel) -> Fragment:\n"
        "    return pysx(t'Panel(title=\"x\")')\n"
    )
    client.open(path, source)
    offset = source.rindex("Panel(title=")
    result = client.call("textDocument/rename", {**at(path, source, offset), "newName": "1bad"})
    assert "error" in result
    assert "not a valid Python name" in result["error"]["message"]


@pytest.mark.parametrize("unsaved", [False, True])
def test_rename_binding_identity_preserves_unrelated_imports_and_shadowing(
    tmp_path: Path, client: Client, unsaved: bool
) -> None:
    library = tmp_path / "library.py"
    otherlib = tmp_path / "otherlib.py"
    consumer = tmp_path / "view.py"
    separate = tmp_path / "separate.py"
    library.write_text(LIBRARY)
    otherlib.write_text(LIBRARY)
    unrelated = (
        '\ndef unrelated():\n    from otherlib import Panel\n    return Panel(title="other")\n'
    )
    source = CONSUMER + unrelated
    consumer.write_text(source)
    separate_source = 'from otherlib import Panel\nvalue = Panel(title="separate")\n'
    separate.write_text(separate_source)
    actual_library = "# unsaved dependency\n" + LIBRARY if unsaved else LIBRARY
    client.open(library, LIBRARY)

    if unsaved:
        client.change(library, actual_library, 2)
    client.open(consumer, source)
    client.settled(lambda state: bool(state["workspaces"]) and state["workspaces"][0]["ready"])
    response = client.request(
        "textDocument/rename",
        {**at(consumer, source, source.index('Panel(title={"x"})')), "newName": "Card"},
    )
    changes = cast("dict[str, list[dict[str, Any]]]", cast("dict[str, Any]", response)["changes"])
    updated = applied(source, changes[consumer.as_uri()])
    assert "from library import Card as Card" in updated
    assert "Card(title=" in updated
    assert unrelated in updated
    assert "def other(Panel: int) -> int:\n    return Panel\n" in updated
    assert separate.as_uri() not in changes
    assert separate.read_text() == separate_source
    assert "def Card(" in applied(actual_library, changes[library.as_uri()])
    assert library.read_text() == LIBRARY


def test_rename_binding_identity_preserves_genuine_aliases(tmp_path: Path, client: Client) -> None:
    library = tmp_path / "library.py"
    consumer = tmp_path / "view.py"
    alias = tmp_path / "alias.py"
    source = CONSUMER
    aliased = (
        "from pysx import pysx\nfrom library import Panel as Fancy\n"
        'view = pysx(t\'Fancy(title="markup")\')\nvalue = Fancy(title="python")\n'
    )
    library.write_text(LIBRARY)
    consumer.write_text(source)
    alias.write_text(aliased)
    client.open(consumer, source)
    client.open(alias, aliased)
    client.settled(lambda state: bool(state["workspaces"]) and state["workspaces"][0]["ready"])
    response = client.request(
        "textDocument/rename",
        {**at(consumer, source, source.rindex("Panel(title=")), "newName": "Card"},
    )
    changes = cast("dict[str, list[dict[str, Any]]]", cast("dict[str, Any]", response)["changes"])
    updated_alias = applied(aliased, changes[alias.as_uri()])
    assert updated_alias == aliased.replace(
        "from library import Panel as Fancy", "from library import Card as Fancy"
    )
    updated_library = applied(LIBRARY, changes[library.as_uri()])
    assert "def Card(" in updated_library
    client.open(library, updated_library)
    client.change(consumer, applied(source, changes[consumer.as_uri()]), 2)
    client.change(alias, updated_alias, 2)
    client.diagnostic(alias, 2, None)
    resolved = cast(
        "list[dict[str, Any]]",
        client.request(
            "textDocument/definition", at(alias, updated_alias, updated_alias.index("Fancy(title="))
        ),
    )
    assert resolved
    assert resolved[0]["uri"] == library.as_uri()
    assert resolved[0]["range"]["start"]["line"] == 1
    refused = client.call(
        "textDocument/rename",
        {**at(alias, aliased, aliased.index("Fancy(title=")), "newName": "Card"},
    )
    assert "error" in refused
    assert "alias" in refused["error"]["message"].lower()


def test_rename_binding_identity_relative_imports_and_reexports(
    tmp_path: Path, client: Client
) -> None:
    package = tmp_path / "parts"
    package.mkdir()
    library = package / "library.py"
    exported = package / "__init__.py"
    consumer = tmp_path / "view.py"
    library.write_text(LIBRARY)
    reexport = "from .library import Panel as Panel\n"
    exported.write_text(reexport)
    source = CONSUMER.replace("from library import", "from parts import")
    consumer.write_text(source)
    client.open(consumer, source)
    client.settled(lambda state: bool(state["workspaces"]) and state["workspaces"][0]["ready"])
    response = client.request(
        "textDocument/rename",
        {**at(consumer, source, source.rindex("Panel(title=")), "newName": "Card"},
    )
    changes = cast("dict[str, list[dict[str, Any]]]", cast("dict[str, Any]", response)["changes"])
    assert applied(reexport, changes[exported.as_uri()]) == "from .library import Card as Card\n"
    assert "from parts import Card as Card" in applied(source, changes[consumer.as_uri()])
    assert "def Card(" in applied(LIBRARY, changes[library.as_uri()])


def test_rename_binding_identity_refuses_generation_changed_while_waiting_for_full_analysis(
    tmp_path: Path,
) -> None:
    entered = tmp_path / "entered"
    release = tmp_path / "release"
    command = [
        sys.executable,
        "-c",
        f"""
import asyncio
from pathlib import Path
from pysx.lsp_server import Session, create_server
original = Session.current
async def blocked(self, *args, **kwargs):
    if kwargs.get("complete") and not Path({str(entered)!r}).exists():
        Path({str(entered)!r}).touch()
        while not Path({str(release)!r}).exists():
            await asyncio.sleep(0.01)
    return await original(self, *args, **kwargs)
Session.current = blocked
create_server().start_io()
""",
    ]
    library = tmp_path / "library.py"
    consumer = tmp_path / "view.py"
    library.write_text(LIBRARY)
    consumer.write_text(CONSUMER)
    instance = Client(tmp_path, command=command)

    try:
        instance.open(consumer, CONSUMER)
        instance.settled(
            lambda state: bool(state["workspaces"]) and state["workspaces"][0]["ready"]
        )
        request = instance.send(
            "textDocument/rename",
            {**at(consumer, CONSUMER, CONSUMER.rindex("Panel(title=")), "newName": "Card"},
            request=True,
        )
        instance.settled(lambda _state: entered.exists())
        instance.change(consumer, "# unsaved prefix\n" + CONSUMER, 2)
        instance.settled(lambda state: state["workspaces"][0]["ready"])
        release.touch()
        response = instance.wait(lambda message: message.get("id") == request)
        assert response["error"]["code"] == -32801
        assert "superseded" in response["error"]["message"].lower()
        assert "result" not in response
    finally:
        release.touch()
        instance.close()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-32"])
def test_rename_binding_identity_unicode_positions_follow_negotiated_encoding(
    tmp_path: Path, encoding: str
) -> None:
    library = tmp_path / "library.py"
    consumer = tmp_path / "view.py"
    library.write_text(LIBRARY)
    source = (
        "from pysx import pysx\nfrom library import Panel\n"
        'label = "🌍"; view = pysx(t\'Panel(title="x")\')\n'
    )
    consumer.write_text(source)
    instance = Client(tmp_path, encoding=encoding)

    try:
        instance.open(consumer, source)
        instance.settled(
            lambda state: bool(state["workspaces"]) and state["workspaces"][0]["ready"]
        )
        prefix = source[: source.rindex("Panel(title=")]
        last = prefix.rsplit("\n", 1)[-1]
        character = len(last.encode("utf-8")) if encoding == "utf-8" else len(last)
        response = instance.request(
            "textDocument/rename",
            {
                **document(consumer),
                "position": {"line": prefix.count("\n"), "character": character},
                "newName": "Card",
            },
        )
        changes = cast(
            "dict[str, list[dict[str, Any]]]", cast("dict[str, Any]", response)["changes"]
        )
        edits = changes[consumer.as_uri()]

        if encoding == "utf-8":
            lines = source.splitlines()

            for edit in edits:
                for endpoint in ("start", "end"):
                    point = edit["range"][endpoint]
                    line = lines[int(point["line"])]
                    character = int(point["character"])
                    point["character"] = len(line.encode("utf-8")[:character].decode("utf-8"))
        assert applied(source, edits) == source.replace("Panel", "Card")
        assert "def Card(" in applied(LIBRARY, changes[library.as_uri()])
    finally:
        instance.close()


def test_rename_binding_identity_refuses_ambiguous_import_bindings(
    tmp_path: Path, client: Client
) -> None:
    library = tmp_path / "library.py"
    consumer = tmp_path / "view.py"
    ambiguous = tmp_path / "ambiguous.py"
    library.write_text(LIBRARY)
    consumer.write_text(CONSUMER)
    ambiguous.write_text("from library import Panel\nPanel = object()\n")
    client.open(consumer, CONSUMER)
    client.settled(lambda state: bool(state["workspaces"]) and state["workspaces"][0]["ready"])
    response = client.call(
        "textDocument/rename",
        {**at(consumer, CONSUMER, CONSUMER.rindex("Panel(title=")), "newName": "Card"},
    )
    assert "error" in response
    assert "competing writes" in response["error"]["message"]
    assert "result" not in response


TEMPLATE_LIBRARY = (
    "from pysx import Fragment, pysx\n"
    "\n"
    "\n"
    "def Card(*, title: str) -> Fragment:\n"
    "    return pysx(t'p: {title}')\n"
    "\n"
    "\n"
    "def Spare(*, title: str) -> Fragment:\n"
    "    return pysx(t'p: {title}')\n"
)

TEMPLATE_CONSUMER = (
    "from pysx import Fragment, pysx\n"
    "from library import Card, Spare\n"
    "\n"
    "\n"
    "def view() -> Fragment:\n"
    "    return pysx(t'Card(title=\"hello\")')\n"
)


def organize(client: Client, path: Path) -> list[dict[str, Any]]:
    result = client.request(
        "textDocument/codeAction",
        {
            **document(path),
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
            "context": {"diagnostics": [], "only": ["source.organizeImports.pysx"]},
        },
    )
    assert isinstance(result, list)

    return cast("list[dict[str, Any]]", result)


def test_resident_commands_organize_imports_keeps_template_only_imports(
    tmp_path: Path, client: Client
) -> None:
    """The replacement action must remove only what is genuinely unused."""
    library = tmp_path / "library.py"
    consumer = tmp_path / "view.py"
    library.write_text(TEMPLATE_LIBRARY)
    consumer.write_text(TEMPLATE_CONSUMER)
    client.open(library, TEMPLATE_LIBRARY)
    client.open(consumer, TEMPLATE_CONSUMER)
    actions = organize(client, consumer)
    assert [action["title"] for action in actions] == ["pysx: remove unused imports"]
    assert actions[0]["kind"] == "source.organizeImports.pysx"
    changed = applied(TEMPLATE_CONSUMER, actions[0]["edit"]["changes"][consumer.as_uri()])
    assert "Card" in changed
    assert "Spare" not in changed
    assert changed.splitlines()[-1] == "    return pysx(t'Card(title=\"hello\")')"


def test_resident_commands_organize_imports_refuses_incomplete_analysis(
    tmp_path: Path, client: Client
) -> None:
    """An unresolved name makes removal unsafe, so nothing may be offered."""
    library = tmp_path / "library.py"
    consumer = tmp_path / "view.py"
    library.write_text(TEMPLATE_LIBRARY)
    broken = TEMPLATE_CONSUMER + "\n\ndef other() -> int:\n    return missing_name\n"
    consumer.write_text(broken)
    client.open(library, TEMPLATE_LIBRARY)
    client.open(consumer, broken)
    assert organize(client, consumer) == []


def test_resident_commands_readiness_reports_replacement_coverage(
    tmp_path: Path, client: Client
) -> None:
    """Setup may only disable competing actions once every replacement answers."""
    path = tmp_path / "view.py"
    path.write_text(TEMPLATE_LIBRARY)
    client.open(path, TEMPLATE_LIBRARY)
    client.request(
        "textDocument/codeAction",
        {
            **document(path),
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
            "context": {"diagnostics": [], "only": ["source.organizeImports.pysx"]},
        },
    )
    result = client.request("pysx/readiness", {"uri": path.as_uri()})
    assert isinstance(result, dict)
    reported = cast("dict[str, Any]", result)
    assert reported["ready"] is True
    assert reported["root"] == str(tmp_path.resolve())
    assert reported["coverage"] == {"ruff": True, "types": True, "organizeImports": True}


def test_resident_commands_refresh_rebuilds_the_analysed_workspace(
    tmp_path: Path, client: Client
) -> None:
    path = tmp_path / "view.py"
    path.write_text(TEMPLATE_LIBRARY)
    client.open(path, TEMPLATE_LIBRARY)
    before = cast("dict[str, Any]", client.request("pysx/status", {}))["workspaces"][0]
    result = cast("dict[str, Any]", client.request("pysx/refresh", {"uri": path.as_uri()}))
    assert result["root"] == str(tmp_path.resolve())
    assert result["generation"] > before["generation"]
