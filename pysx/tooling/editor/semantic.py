"""Query frozen worker mirrors using their own tracked analysis workspace."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast

from lsprotocol import types

from pysx.compiler.projects import import_roots
from pysx.tooling.editor.types import owned_revision
from pysx.tooling.lsp.backend import TypeBackend
from pysx.tooling.lsp.workspace import path_for_uri, project_settings


class Request(TypedDict):
    file: str
    position: dict[str, int]
    newName: NotRequired[str]


async def query(
    directory: Path,
    root: Path,
    request: Request,
    operation: Literal["rename", "definition"] = "rename",
) -> list[dict[str, object]]:
    directory, root = directory.resolve(), root.resolve()
    target = Path(request["file"]).resolve()

    if not owned_revision(directory, root) or not target.is_relative_to(directory):
        raise ValueError("rename requires an owned workspace revision")
    files = sorted(directory.rglob("*.py"))

    if target not in files or not 1 <= len(files) <= 513:
        raise ValueError("invalid rename input coverage")
    original = root / "pyrightconfig.json"
    config = project_settings(original if original.is_file() else root / "pyproject.toml")
    extra = cast("list[str]", config.get("extraPaths", []))
    config.update(
        include=["."],
        exclude=[],
        ignore=[],
        extraPaths=[str(directory.parent), *map(str, import_roots(root)), *extra],
    )
    configuration = directory / "pyrightconfig.json"
    backend = TypeBackend(lambda _params: None, lambda: None)

    with configuration.open("x", encoding="utf-8") as stream:
        json.dump(config, stream)

    try:
        await backend.start(directory, sys.executable)

        for file in files:
            backend.client.text_document_did_open(
                types.DidOpenTextDocumentParams(
                    types.TextDocumentItem(
                        file.as_uri(), "python", 1, file.read_text(encoding="utf-8")
                    )
                )
            )

        if operation == "definition":
            locations = await backend.definition(
                target.as_uri(), types.Position(**request["position"])
            )

            if locations is None:
                return []

            if isinstance(locations, types.Location):
                locations = [locations]
            definitions: list[dict[str, object]] = []

            for location in locations:
                uri = location.uri if isinstance(location, types.Location) else location.target_uri
                span = (
                    location.range
                    if isinstance(location, types.Location)
                    else location.target_selection_range
                )
                definitions.append(
                    {
                        "file": str(path_for_uri(uri)),
                        "range": {
                            "start": {"line": span.start.line, "character": span.start.character},
                            "end": {"line": span.end.line, "character": span.end.character},
                        },
                    }
                )

            return definitions
        name = request.get("newName")

        if name is None:
            raise ValueError("rename requires a new name")
        result = await backend.request(
            backend.client.text_document_rename_async(
                types.RenameParams(
                    types.TextDocumentIdentifier(target.as_uri()),
                    types.Position(**request["position"]),
                    name,
                )
            )
        )

        if result is None:
            raise ValueError("type backend could not rename this symbol")
        edits: list[dict[str, object]] = []

        for change in result.document_changes or ():
            if not isinstance(change, types.TextDocumentEdit):
                raise ValueError("rename cannot change workspace files")
            file = path_for_uri(change.text_document.uri)

            if file not in files:
                raise ValueError("rename escaped its workspace revision")
            changes: list[dict[str, object]] = []

            for edit in change.edits:
                if not isinstance(edit, (types.TextEdit, types.AnnotatedTextEdit)):
                    raise ValueError("rename cannot insert snippets")
                changes.append(
                    {
                        "range": {
                            "start": {
                                "line": edit.range.start.line,
                                "character": edit.range.start.character,
                            },
                            "end": {
                                "line": edit.range.end.line,
                                "character": edit.range.end.character,
                            },
                        },
                        "newText": edit.new_text,
                    }
                )
            edits.append(
                {
                    "file": str(file),
                    "edits": changes,
                }
            )

        if result.changes:
            raise ValueError("unexpected unversioned rename edits")

        return edits
    finally:
        try:
            await backend.close()
        finally:
            configuration.unlink(missing_ok=True)


def main() -> None:
    request = cast("Request", json.load(sys.stdin))
    operation = sys.argv[3] if len(sys.argv) > 3 else "rename"

    if operation not in {"rename", "definition"}:
        raise ValueError("unknown semantic operation")
    result = asyncio.run(
        query(
            Path(sys.argv[1]),
            Path(sys.argv[2]),
            request,
            cast("Literal['rename', 'definition']", operation),
        )
    )
    sys.stdout.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
