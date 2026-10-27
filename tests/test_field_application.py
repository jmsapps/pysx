"""Installed tutorial wheel and real downstream artifact qualification."""

import subprocess
import zipfile
from typing import TYPE_CHECKING

import pytest
from browser_tutorial_fixture import TutorialWorker
from prototypes.distribution import ROOT, clean_env

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.acceptance
def test_validate_integrated_tutorial_wheel_assets_types_and_provenance(tmp_path: Path) -> None:
    worker = TutorialWorker(tmp_path, packaged=True)
    assert worker.wheel is not None
    with zipfile.ZipFile(worker.wheel) as archive:
        names = archive.namelist()
        assert "tutorial/py.typed" in names
        assert "tutorial/assets/course.css" in names
        assert "tutorial/assets/code-tokens.json" in names
        assert "tutorial/components/pages.py" in names
        assert not any(name.startswith(("examples/", "tests/")) for name in names)
    worker.reinstall()
    result = subprocess.run(
        [
            str(worker.python),
            "-I",
            "-c",
            "import importlib.resources,pathlib,sys,tutorial,pysx; "
            "assert pathlib.Path(tutorial.__file__).is_relative_to(pathlib.Path(sys.prefix)); "
            "assert pathlib.Path(pysx.__file__).is_relative_to(pathlib.Path(sys.prefix)); "
            "assert importlib.resources.files('tutorial').joinpath('py.typed').is_file(); "
            "assert not any(p == sys.argv[1] or p.startswith(sys.argv[1] + '/') for p in sys.path)",
            str(ROOT),
        ],
        cwd=tmp_path,
        env=clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def prepare_editor(workspace: Path) -> dict[str, str]:
    worker = TutorialWorker(workspace, packaged=True, editor=True)
    consumer = workspace / "consumer"
    consumer.mkdir()

    return {"consumer": str(consumer), "python": str(worker.python)}
