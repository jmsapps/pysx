"""Original-buffer completion, hover and navigation through the resident protocol."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, cast

import pytest
from test_lsp import Client

from pysx.compiler.compiler import analyze
from pysx.tooling.analysis import projection_for_source
from pysx.tooling.lsp.features import classify, template_calls
from pysx.tooling.lsp.workspace import position

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.mark.parametrize(
    ("declaration", "call", "expected"),
    [
        ("from pysx import pysx", "pysx", True),
        ("from pysx import pysx as template", "template", True),
        ("from pysx.rendering.render import pysx", "pysx", True),
        ("import pysx as px", "px.pysx", True),
        ("import pysx.rendering.render as renderer", "renderer.pysx", True),
        ("from pysx import pysx\ntemplate = pysx", "template", True),
        ("from unrelated import pysx", "pysx", False),
        ("def pysx(value): return value", "pysx", False),
        ("from pysx import pysx\npysx = lambda value: value", "pysx", False),
        ("from pysx.compiler.template import pysx", "pysx", False),
    ],
)
def test_render_marker_paths(tmp_path: Path, declaration: str, call: str, expected: bool) -> None:
    source = f'{declaration}\nview = {call}(t"Panel")\n'
    filename = str(tmp_path / "view.py")
    assert bool(analyze(source, filename).calls) is expected
    assert (
        classify(source, source.index("Panel") + 2, filename, tmp_path, {}) is not None
    ) is expected
    assert bool(template_calls(source, filename, tmp_path, {})) is expected


@pytest.fixture
def client(tmp_path: Path) -> Iterator[Client]:
    instance = Client(tmp_path)

    try:
        yield instance
    finally:
        instance.close()


def params(path: Path, source: str, at: int) -> dict[str, object]:
    point = position(source, at)

    return {
        "textDocument": {"uri": path.as_uri()},
        "position": {"line": point.line, "character": point.character},
    }


def complete(client: Client, path: Path, source: str, at: int) -> list[dict[str, Any]]:
    result = client.request("textDocument/completion", params(path, source, at))
    assert isinstance(result, list)

    return cast("list[dict[str, Any]]", result)


@pytest.mark.parametrize(
    ("declaration", "call"),
    [
        ("from pysx import pysx", "pysx"),
        ("from pysx.rendering.render import pysx", "pysx"),
        ("import pysx.rendering.render as renderer", "renderer.pysx"),
    ],
)
def test_marker_completion_and_rename(
    tmp_path: Path, client: Client, declaration: str, call: str
) -> None:
    source = (
        f"{declaration}\nfrom pysx import Fragment\n"
        "def Panel(*, title: str) -> Fragment:\n"
        f"    return {call}(t'p: {{title}}')\n"
        f"view = {call}(t'Panel(title=\"hello\")')\n"
    )
    path = tmp_path / "view.py"
    client.open(path, source)
    at = source.rindex("title=")
    assert any(item["label"] == "title" for item in complete(client, path, source, at))
    result = client.request(
        "textDocument/rename",
        {**params(path, source, source.rindex("Panel(")), "newName": "Card"},
    )
    assert isinstance(result, dict)
    edits = cast("dict[str, Any]", result)["changes"][path.as_uri()]
    assert len(edits) == 2
    assert all(edit["newText"] == "Card" for edit in edits)


def test_buffer_readiness_definition_precedes_unrelated_sweep(tmp_path: Path) -> None:
    unrelated = tmp_path / "unrelated.py"
    unrelated.write_text("value = 1\n")
    entered = tmp_path / "entered"
    release = tmp_path / "release"
    command = [
        sys.executable,
        "-c",
        f"""
import time
from pathlib import Path
from pysx.tooling.lsp.workspace import WorkspaceIndex
from pysx.tooling.lsp.server import create_server
original = WorkspaceIndex.build
def blocked(self, *args, **kwargs):
    if {str(unrelated)!r} in kwargs.get("selected", set()):
        Path({str(entered)!r}).touch()
        while not Path({str(release)!r}).exists():
            time.sleep(0.01)
    return original(self, *args, **kwargs)
WorkspaceIndex.build = blocked
create_server().start_io()
""",
    ]
    instance = Client(tmp_path, command=command)
    path = tmp_path / "view.py"
    source = (
        "from pysx import Fragment, pysx\n"
        "def Panel(*, title: str) -> Fragment:\n"
        "    return pysx(t'p: {title}')\n"
        "view = pysx(t'Panel(title=\"hello\")')\n"
    )

    try:
        instance.open(path, source)
        instance.settled(lambda _state: entered.exists())
        assert instance.status()["workspaces"][0]["ready"] is False
        request = params(path, source, source.rindex("Panel("))
        result = cast("list[dict[str, Any]]", instance.request("textDocument/definition", request))
        assert result
        assert result[0]["uri"] == path.as_uri()
        assert result[0]["range"]["start"]["line"] == 1
        refused = instance.call("textDocument/rename", {**request, "newName": "Card"})
        assert refused["error"]["code"] == -32801
        assert "not ready" in refused["error"]["message"]
        assert not release.exists()
        release.touch()
        instance.settled(lambda state: state["workspaces"][0]["ready"] is True)
        assert instance.request("textDocument/rename", {**request, "newName": "Card"})
        assert not any(
            "Ignoring notification for unknown method 'pyright/" in error
            for error in instance.errors
        )
    finally:
        release.touch()
        instance.close()


def test_buffer_readiness_backend_failure_is_actionable_and_recovers(tmp_path: Path) -> None:
    recovered = tmp_path / "recover"
    command = [
        sys.executable,
        "-c",
        f"""
from pathlib import Path
from pysx.tooling.lsp.backend import TypeBackend
from pysx.tooling.lsp.server import create_server
original = TypeBackend.start
async def start(self, *args, **kwargs):
    if not Path({str(recovered)!r}).exists():
        raise RuntimeError("type service startup probe failed")
    return await original(self, *args, **kwargs)
TypeBackend.start = start
create_server().start_io()
""",
    ]
    instance = Client(tmp_path, command=command)
    path = tmp_path / "view.py"
    source = "from pysx import pysx\nview = pysx(t'div: hello')\n"

    try:
        instance.open(path, source)
        instance.settled(lambda state: bool(state["workspaces"][0]["error"]))
        result = instance.call("textDocument/hover", params(path, source, source.index("div")))
        assert result["error"]["code"] == -32001
        assert "startup probe failed" in result["error"]["message"]
        assert instance.request(
            "textDocument/semanticTokens/full", {"textDocument": {"uri": path.as_uri()}}
        )
        recovered.touch()
        instance.change(path, source, 2)
        instance.settled(
            lambda state: state["workspaces"][0]["ready"] and not state["workspaces"][0]["error"]
        )
        assert instance.hover(path, 1, source.splitlines()[1].index("div"))
    finally:
        recovered.touch()
        instance.close()


def test_buffer_readiness_unqualified_lint_keeps_every_other_feature(tmp_path: Path) -> None:
    """An unusable Ruff must cost its own findings, not completions, hover and rename."""
    command = [
        sys.executable,
        "-c",
        """
import pysx.tooling.tools as tools
tools._candidates = lambda interpreter: []
from pysx.tooling.lsp.server import create_server
create_server().start_io()
""",
    ]
    instance = Client(tmp_path, command=command)
    path = tmp_path / "view.py"
    source = (
        "import json\n"
        "from pysx import Fragment, pysx\n"
        "def Panel(*, title: str) -> Fragment:\n"
        "    return pysx(t'p: {title}')\n"
        "view = pysx(t'Panel(missing=\"hello\")')\n"
    )

    try:
        instance.open(path, source)
        state = instance.settled(lambda status: status["workspaces"][0]["ready"] is True)
        assert state["workspaces"][0]["error"] is None
        readiness = cast(
            "dict[str, Any]", instance.request("pysx/readiness", {"uri": path.as_uri()})
        )
        assert readiness["ready"] is True
        assert readiness["error"] is None
        assert readiness["coverage"]["ruff"] is False
        published = instance.diagnostic(path, 1, "component-unknown-prop")
        assert not any(item["code"] == "F401" for item in published["diagnostics"])
        assert instance.hover(path, 4, source.splitlines()[4].index("Panel"))
        request = params(path, source, source.rindex("Panel("))
        assert instance.request("textDocument/rename", {**request, "newName": "Card"})
        assert instance.request("textDocument/definition", request)
    finally:
        instance.close()


@pytest.mark.parametrize("ending", ["Panel()')", "Panel(ma"])
def test_completion_hover_typed_callable_parameter(
    tmp_path: Path,
    client: Client,
    ending: str,
) -> None:
    path = tmp_path / "view.py"
    marker = tmp_path / "executed"
    source = (
        "from typing import Protocol\n"
        "from pathlib import Path\n"
        "from pysx import Fragment, pysx\n"
        f"Path({str(marker)!r}).touch()\n"
        "class Renderer(Protocol):\n"
        "    def __call__(self, *, title: str, maxWidth: int = 80) -> Fragment: ...\n"
        "def page(Panel: Renderer) -> Fragment:\n"
        "    return pysx(t'" + ending
    )
    client.open(path, source)
    at = source.rindex("Panel(") + len("Panel(")

    if ending.endswith("ma"):
        at = len(source)
    result = complete(client, path, source, at)
    assert "maxWidth" in {item["label"] for item in result}
    help_result = client.request("textDocument/signatureHelp", params(path, source, at))
    assert "maxWidth: int = 80" in str(help_result)
    assert not marker.exists()


@pytest.mark.parametrize("body", ["input(pla", 'input(type="text" pla', "input(\n    pla"])
def test_completion_hover_incomplete_native_attribute(
    tmp_path: Path,
    client: Client,
    body: str,
) -> None:
    path = tmp_path / "view.py"
    source = "from pysx import pysx as render\nview = render(t'''" + body
    client.open(path, source)
    result = complete(client, path, source, len(source))
    assert [item["label"] for item in result] == ["placeholder"]
    edit = result[0]["textEdit"]
    start = position(source, source.rindex("pla"))
    assert edit["newText"] == "placeholder"
    assert edit["range"]["start"] == {"line": start.line, "character": start.character}
    assert all("additionalTextEdits" not in item for item in result)


def test_completion_hover_incomplete_tag_and_exact_replacement(
    tmp_path: Path, client: Client
) -> None:
    path = tmp_path / "view.py"
    source = "from pysx import pysx\nemoji = '😀'\nview = pysx(t'inp"
    client.open(path, source)
    result = complete(client, path, source, len(source))
    assert "input" in {item["label"] for item in result}
    assert all(
        item["textEdit"]["range"]["end"]["character"] == len(source.splitlines()[2])
        for item in result
    )


def test_completion_hover_native_attribute_families_and_signature(
    tmp_path: Path, client: Client
) -> None:
    path = tmp_path / "view.py"
    source = "from pysx import pysx\nview = pysx(t'input()')\n"
    client.open(path, source)
    point = source.index("()") + 1
    inputs = complete(client, path, source, point)
    assert {"placeholder", "checked", "bind_value", "bind_checked"} <= {
        item["label"] for item in inputs
    }
    assert "bind_selected" not in {item["label"] for item in inputs}
    help_result = client.request("textDocument/signatureHelp", params(path, source, point))
    assert isinstance(help_result, dict)
    assert "placeholder" in str(cast("dict[str, Any]", help_result))
    assert "children" in str(cast("dict[str, Any]", help_result))
    button = source.replace("input", "button")
    client.change(path, button, 2)
    buttons = complete(client, path, button, button.index("()") + 1)
    names = {item["label"] for item in buttons}
    assert "placeholder" not in names
    assert "checked" not in names
    assert "bind_value" not in names
    hover = client.request("textDocument/hover", params(path, button, button.index("button") + 2))
    assert "<button>" in str(hover)


def test_completion_hover_callable_children_props_and_casing(
    tmp_path: Path, client: Client
) -> None:
    path = tmp_path / "view.py"
    source = (
        "from pysx import Children, Fragment, pysx\n"
        "def Panel(*, title: str, maxWidth: int = 80, "
        "children: Children | None = None) -> Fragment:\n"
        "    return pysx(t'p: {title}')\n"
        "view = pysx(t'Panel()')\n"
    )
    client.open(path, source)
    point = source.rindex("()") + 1
    result = complete(client, path, source, point)
    assert {"title", "maxWidth", "children"} == {item["label"] for item in result}
    help_result = client.request("textDocument/signatureHelp", params(path, source, point))
    assert "maxWidth: int = 80" in str(help_result)
    assert "Children | None = None" in str(help_result)


@pytest.mark.parametrize("region", ["expression", "deferred", "text", "style", "python"])
def test_completion_hover_python_text_and_style_ownership(
    tmp_path: Path,
    client: Client,
    region: str,
) -> None:
    path = tmp_path / "view.py"
    source = (
        "from pysx import defer, pysx, signal, styled\n"
        "count = signal(1)\n"
        "Panel = styled.p(t'color: red;')\n"
        "view = pysx(t'p: \"ordinary text\" {defer(count, lambda value: value)}')\n"
    )
    client.open(path, source)
    at = {
        "expression": source.rindex("count") + 2,
        "deferred": source.rindex("lambda") + 2,
        "text": source.index("ordinary") + 2,
        "style": source.index("color") + 2,
        "python": source.index("signal(1)") + 2,
    }[region]
    assert complete(client, path, source, at) == []
    assert client.request("textDocument/hover", params(path, source, at)) is None
    assert client.request("textDocument/definition", params(path, source, at)) == []


def test_completion_hover_repeated_styled_sites_preserve_binding(tmp_path: Path) -> None:
    source = (
        "from pysx import styled, pysx\nDescription = styled.p(t'color: red;')\n"
        "view = pysx(t'''\nDescription: \"one\"\n"
        'Description: "two"\nDescription: "three"\n\'\'\')\n'
    )
    model = projection_for_source(source, str(tmp_path / "repeated_styled_sites.py"))
    sites = [
        site for site in model["sites"] if source[site["start"] : site["end"]] == "Description"
    ]
    assert len(sites) == 3
    assert len({site["start"] for site in sites}) == 3
    assert len({site["generated"] for site in sites}) == 1
    assert model["text"][sites[0]["generated"] :].startswith("Description")


def test_completion_hover_repeated_styled_navigation_and_references(
    tmp_path: Path,
    client: Client,
) -> None:
    path = tmp_path / "view.py"
    source = (
        "from pysx import styled, pysx\nDescription = styled.p(t'color: red;')\n"
        "view = pysx(t'''\nDescription: \"one\"\n"
        'Description: "two"\nDescription: "three"\n\'\'\')\n'
    )
    client.open(path, source)
    sites = [match for match in range(len(source)) if source.startswith("Description:", match)]

    for at in sites:
        definition = client.request("textDocument/definition", params(path, source, at + 2))
        assert isinstance(definition, list)
        assert definition[0]["uri"] == path.as_uri()
        assert definition[0]["range"]["start"]["line"] == 1
        hover = client.request("textDocument/hover", params(path, source, at + 2))
        assert "Description" in str(hover)
        assert "_pysx_revision" not in str(hover)
    request = params(path, source, sites[-1] + 2)
    request["context"] = {"includeDeclaration": True}
    references = client.request("textDocument/references", request)
    assert isinstance(references, list)
    values = cast("list[dict[str, Any]]", references)
    rows = {item["range"]["start"]["line"] for item in values if item["uri"] == path.as_uri()}
    assert {1, 3, 4, 5} <= rows


def test_completion_hover_import_alias_unsaved_definition_updates(
    tmp_path: Path, client: Client
) -> None:
    producer = tmp_path / "parts.py"
    declaration = "from pysx import styled\nDescription = styled.p(t'color: red;')\n"
    producer.write_text(declaration)
    path = tmp_path / "view.py"
    source = (
        "from pysx import pysx\nfrom parts import Description as Note\n"
        "view = pysx(t'''\nNote: \"one\"\nNote: \"two\"\n''')\n"
    )
    client.open(path, source)
    point = source.rindex("Note:") + 1
    initial = client.request("textDocument/definition", params(path, source, point))
    assert isinstance(initial, list)
    assert initial[0]["uri"] == producer.as_uri()
    assert initial[0]["range"]["start"]["line"] == 1
    client.open(producer, declaration)
    client.change(producer, "\n" + declaration, 2)
    changed = client.request("textDocument/definition", params(path, source, point))
    assert isinstance(changed, list)
    assert changed[0]["range"]["start"]["line"] == 2


def test_completion_hover_nested_scopes_and_multiple_templates(
    tmp_path: Path,
    client: Client,
) -> None:
    path = tmp_path / "view.py"
    source = (
        "from pysx import styled, pysx\n"
        "Description = styled.p(t'color: red;')\n"
        "first = pysx(t'''\nDescription: \"one\"\nDescription: \"two\"\n''')\n"
        "def nested():\n"
        "    Description = styled.span(t'color: blue;')\n"
        "    return pysx(t'''\nDescription: \"three\"\nDescription: \"four\"\n''')\n"
        "last = pysx(t'Description: \"five\"')\n"
    )
    client.open(path, source)
    points = [index for index in range(len(source)) if source.startswith("Description:", index)]

    for at, row in zip(points, [1, 1, 7, 7, 1], strict=True):
        locations = client.request("textDocument/definition", params(path, source, at + 2))
        assert isinstance(locations, list)
        assert locations[0]["range"]["start"]["line"] == row
    request = params(path, source, points[-1] + 2)
    request["context"] = {"includeDeclaration": False}
    references = cast("list[dict[str, Any]]", client.request("textDocument/references", request))
    rows = {item["range"]["start"]["line"] for item in references if item["uri"] == path.as_uri()}
    assert {3, 4, 12} <= rows
    assert not rows.intersection({9, 10})


@pytest.mark.parametrize("template", ["t'input(pla'", "t'input(' + t'pla'"])
def test_completion_hover_bound_and_assembled_template(
    tmp_path: Path,
    client: Client,
    template: str,
) -> None:
    path = tmp_path / "view.py"
    source = f"from pysx import pysx\ntemplate = {template}\nview = pysx(template)\n"
    client.open(path, source)
    result = complete(client, path, source, source.index("pla", source.index("input(")) + 3)
    assert [item["label"] for item in result] == ["placeholder"]


def test_completion_hover_escaped_unicode_raw_replacement(tmp_path: Path, client: Client) -> None:
    path = tmp_path / "view.py"
    source = "from pysx import pysx\nemoji = '😀'; view = pysx(t'input(\\u0070la"
    client.open(path, source)
    result = complete(client, path, source, len(source))
    assert [item["label"] for item in result] == ["placeholder"]
    expected = position(source, source.index("\\u0070"))
    assert result[0]["textEdit"]["range"]["start"] == {
        "line": expected.line,
        "character": expected.character,
    }
    assert result[0]["textEdit"]["newText"] == "placeholder"


def test_completion_hover_styled_callable_preserves_props(tmp_path: Path, client: Client) -> None:
    path = tmp_path / "view.py"
    source = (
        "from pysx import Fragment, pysx, styled\n"
        "def Base(*, title: str, maxWidth: int = 80) -> Fragment:\n"
        "    return pysx(t'p: {title}')\n"
        "Panel = styled(Base)(t'color: red;')\n"
        "view = pysx(t'Panel(ma"
    )
    client.open(path, source)
    result = complete(client, path, source, len(source))
    assert [item["label"] for item in result] == ["maxWidth"]
    signature_result = client.request(
        "textDocument/signatureHelp", params(path, source, len(source))
    )
    assert "maxWidth: int = 80" in str(signature_result)


TEMPLATE_ONLY = (
    "from pysx import Fragment, pysx\n"
    "from library import Card, Unused\n"
    "\n"
    "\n"
    "def helper(value: str) -> str:\n"
    "    return value\n"
    "\n"
    "\n"
    "def view() -> Fragment:\n"
    "    local = helper('x')\n"
    "    return pysx(t'Card(title={local})')\n"
)

LIBRARY = (
    "from pysx import Fragment, pysx\n"
    "\n"
    "\n"
    "def Card(*, title: str) -> Fragment:\n"
    "    return pysx(t'p: {title}')\n"
    "\n"
    "\n"
    "def Unused(*, title: str) -> Fragment:\n"
    "    return pysx(t'p: {title}')\n"
)


def diagnostics_of(client: Client, path: Path, version: int) -> list[dict[str, Any]]:
    def settled(message: dict[str, Any]) -> bool:
        params = message.get("params", {})

        return (
            message.get("method") == "textDocument/publishDiagnostics"
            and params.get("uri") == path.as_uri()
            and params.get("version") == version
        )

    published = cast("dict[str, Any]", client.wait(settled)["params"])

    return cast("list[dict[str, Any]]", published["diagnostics"])


def codes(client: Client, path: Path, version: int) -> list[str]:
    return [str(item.get("code")) for item in diagnostics_of(client, path, version)]


def test_editor_surface_template_only_import_is_not_reported_unused(
    tmp_path: Path, client: Client
) -> None:
    """A tag used only inside markup is a real use; reporting it dead is the bug."""
    (tmp_path / "library.py").write_text(LIBRARY)
    path = tmp_path / "view.py"
    client.open(tmp_path / "library.py", LIBRARY)
    client.open(path, TEMPLATE_ONLY)
    reported = diagnostics_of(client, path, 1)
    codes_seen = [str(item.get("code")) for item in reported]
    assert "reportUnusedImport" not in codes_seen
    assert "reportUnusedVariable" not in codes_seen
    # The other half of the contract: a genuinely dead import is still reported,
    # at its own original-source range, so ownership is not blanket suppression.
    dead = [item for item in reported if item.get("code") == "F401"]
    assert len(dead) == 1, codes_seen
    row = int(dead[0]["range"]["start"]["line"])
    assert "Unused" in TEMPLATE_ONLY.splitlines()[row]


def test_editor_surface_unrelated_type_errors_are_preserved(tmp_path: Path, client: Client) -> None:
    """Owning unused-symbol reporting must not silence typing pysx owns.

    Ordinary Python expressions stay with Pylance by design, so the error has to
    sit in a template hole for this server to be the one reporting it.
    """
    (tmp_path / "library.py").write_text(LIBRARY)
    path = tmp_path / "view.py"
    source = TEMPLATE_ONLY.replace("Card(title={local})", "Card(title={42})")
    client.open(tmp_path / "library.py", LIBRARY)
    client.open(path, source)
    published = client.diagnostic(path, 1, "reportArgumentType")
    assert any(item["code"] == "reportArgumentType" for item in published["diagnostics"])
    assert any(item["code"] == "F401" for item in published["diagnostics"])


def test_editor_surface_unsaved_mistake_clears_without_saving(
    tmp_path: Path, client: Client
) -> None:
    (tmp_path / "library.py").write_text(LIBRARY)
    path = tmp_path / "view.py"
    broken = TEMPLATE_ONLY.replace("Card(title=", "Crd(title=")
    client.open(tmp_path / "library.py", LIBRARY)
    client.open(path, broken)
    assert any("component" in code for code in codes(client, path, 1))
    client.change(path, TEMPLATE_ONLY, 2)
    settled = codes(client, path, 2)
    assert not any("component" in code for code in settled)
    assert not path.exists(), "the corrected buffer was never written to disk"


def test_editor_surface_no_duplicate_or_mirror_diagnostics(tmp_path: Path, client: Client) -> None:
    """Projection mirrors must never surface as their own documents."""
    (tmp_path / "library.py").write_text(LIBRARY)
    path = tmp_path / "view.py"
    broken = TEMPLATE_ONLY.replace("helper('x')", "helper(42)")
    client.open(tmp_path / "library.py", LIBRARY)
    client.open(path, broken)
    codes(client, path, 1)
    published = [
        message["params"]["uri"]
        for message in client.history
        if message.get("method") == "textDocument/publishDiagnostics"
    ]
    assert all("_pysx_revision_" not in uri for uri in published)
    assert all(uri in {path.as_uri(), (tmp_path / "library.py").as_uri()} for uri in published)
    ranges = [
        (item["range"]["start"]["line"], item["code"])
        for message in client.history
        if message.get("method") == "textDocument/publishDiagnostics"
        and message["params"]["uri"] == path.as_uri()
        and message["params"].get("version") == 1
        for item in message["params"]["diagnostics"]
    ]
    assert len(ranges) == len(set(ranges)), f"duplicate diagnostics: {ranges}"


def test_editor_surface_requires_no_manual_configuration(tmp_path: Path, client: Client) -> None:
    """A bare workspace with no pyproject or pyrightconfig must still analyze."""
    assert not (tmp_path / "pyproject.toml").exists()
    assert not (tmp_path / "pyrightconfig.json").exists()
    (tmp_path / "library.py").write_text(LIBRARY)
    path = tmp_path / "view.py"
    client.open(tmp_path / "library.py", LIBRARY)
    client.open(path, TEMPLATE_ONLY)
    reported = codes(client, path, 1)
    assert "reportUnusedImport" not in reported
    assert reported == ["F401"], reported


def test_editor_surface_cli_and_editor_agree_on_diagnostics(tmp_path: Path, client: Client) -> None:
    """`python -m pysx.tooling.check` and the resident server must not disagree.

    The CLI reports flat line/char fields and the protocol reports LSP ranges,
    so both are normalized before comparison.
    """
    import json
    import subprocess
    import sys

    path = tmp_path / "view.py"
    source = (
        "from pysx import Fragment, pysx\n"
        "\n"
        "\n"
        "def Panel(*, title: str) -> Fragment:\n"
        "    return pysx(t'p: {title}')\n"
        "\n"
        "\n"
        "view = pysx(t'Pnel(title=\"x\")')\n"
    )
    path.write_text(source)
    client.open(path, source)
    published = client.diagnostic(path, 1, "unknown-component")
    served = sorted(
        (
            str(item["code"]),
            item["range"]["start"]["line"],
            item["range"]["start"]["character"],
            item["range"]["end"]["character"],
        )
        for item in published["diagnostics"]
        if "component" in str(item["code"])
    )
    result = subprocess.run(
        [sys.executable, "-m", "pysx.tooling.check", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    reported = sorted(
        (str(item["code"]), item["line"], item["startChar"], item["endChar"])
        for item in json.loads(result.stdout)
        if "component" in str(item["code"])
    )
    assert served, "the server reported no component diagnostic"
    assert served == reported


@pytest.mark.acceptance
def test_editor_surface_large_workspace_measurements(tmp_path: Path, client: Client) -> None:
    """Record responsiveness on a large workspace; PYSX-22 owns the thresholds."""
    import json
    import os
    import sys
    import time
    from pathlib import Path as FilePath

    leaves = 48
    (tmp_path / "shared.py").write_text(
        "from pysx import Fragment, pysx\n"
        "\n"
        "\n"
        "def Card(*, title: str) -> Fragment:\n"
        "    return pysx(t'p: {title}')\n"
    )
    client.open(tmp_path / "shared.py", (tmp_path / "shared.py").read_text())
    paths: list[Path] = []

    for index in range(leaves):
        path = tmp_path / f"leaf_{index:03d}.py"
        path.write_text(
            "from pysx import Fragment, pysx\n"
            "from shared import Card\n"
            "\n"
            "\n"
            f"def view_{index}() -> Fragment:\n"
            f"    return pysx(t'Card(title=\"leaf {index}\")')\n"
        )
        paths.append(path)
        client.open(path, path.read_text())
    target = paths[0]
    client.diagnostic(target, 1, None)
    began = time.monotonic()
    point = position(target.read_text(), target.read_text().index("Card(title="))
    client.request(
        "textDocument/hover",
        {
            "textDocument": {"uri": target.as_uri()},
            "position": {"line": point.line, "character": point.character},
        },
    )
    hover = time.monotonic() - began
    began = time.monotonic()
    client.change(target, target.read_text().replace("leaf 0", "leaf zero"), 2)
    client.diagnostic(target, 2, None)
    edit = time.monotonic() - began
    status = client.status()["workspaces"][0]
    measurements = {
        "openDocuments": status["openDocuments"],
        "hoverSeconds": round(hover, 3),
        "singleEditSeconds": round(edit, 3),
        "republishedAfterEdit": len(status["republished"]),
    }
    assert measurements["openDocuments"] == leaves + 1
    assert hover < 15, measurements
    assert edit < 30, measurements
    destination = os.environ.get("PYSX_MEASUREMENTS")

    if destination:
        FilePath(destination).write_text(json.dumps(measurements, indent=2))
    sys.stdout.write(f"EDITOR SURFACE MEASUREMENTS: {json.dumps(measurements)}\n")
