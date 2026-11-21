"""Hidden managed snapshots retain semantic, cross-file rename coverage."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from pysx.tooling.editor.semantic import Request, query

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("configured", [False, True])
def test_managed_projection_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: bool
) -> None:
    state = tmp_path / ("custom-state" if configured else ".pysx")

    if configured:
        monkeypatch.setenv("PYSX_STATE_DIR", str(state))
    else:
        monkeypatch.delenv("PYSX_STATE_DIR", raising=False)
    directory = state / "app" / "temporary" / "editor-worker" / "_pysx_revision_test"
    directory.mkdir(parents=True)
    producer = directory / "producer.py"
    producer.write_text("def Panel(*, title: str) -> str:\n    return title\n", encoding="utf-8")
    consumer = directory / "consumer.py"
    source = 'from _pysx_revision_test.producer import Panel\nvalue = Panel(title="Panel quoted")\n'
    consumer.write_text(source, encoding="utf-8")
    (directory / "__init__.py").write_text("", encoding="utf-8")
    request: Request = {
        "file": str(producer),
        "position": {"line": 0, "character": 5},
        "newName": "CardPanel",
    }
    changes = asyncio.run(query(directory, tmp_path, request))
    assert {item["file"] for item in changes} == {str(producer), str(consumer)}
    definition: Request = {"file": str(consumer), "position": {"line": 1, "character": 9}}
    assert asyncio.run(query(directory, tmp_path, definition, "definition")) == [
        {
            "file": str(producer),
            "range": {
                "start": {"line": 0, "character": 4},
                "end": {"line": 0, "character": 9},
            },
        }
    ]
    assert consumer.read_text(encoding="utf-8") == source
    assert not (directory / "pyrightconfig.json").exists()
    assert not (tmp_path / "pysx-analysis").exists()


def test_projection_rename_rejects_unowned_directory(tmp_path: Path) -> None:
    directory = tmp_path / "outside" / "_pysx_revision_test"
    directory.mkdir(parents=True)
    request: Request = {
        "file": str(directory / "producer.py"),
        "position": {"line": 0, "character": 5},
        "newName": "CardPanel",
    }
    with pytest.raises(ValueError, match="owned workspace revision"):
        asyncio.run(query(directory, tmp_path, request))
