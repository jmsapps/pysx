"""Scaffolded playground sources, scoped replacement and per-playground state."""

import subprocess
import sys
from pathlib import Path

import pytest

from pysx.playgrounds import NAMES
from pysx.tooling.playground import scaffold, unknown

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "pysx" / "playgrounds"


def test_every_name_ships_a_source() -> None:
    for name in NAMES:
        assert (SHIPPED / f"{name}.py").is_file()


def test_shipped_sources_import_only_the_public_api() -> None:
    for name in NAMES:
        text = (SHIPPED / f"{name}.py").read_text(encoding="utf-8")

        for line in text.splitlines():
            if line.startswith(("import ", "from ")) and "pysx" in line:
                assert line.startswith(("from pysx import", "from pysx.runtime.wire import")), line


def test_scaffold_writes_every_playground(tmp_path: Path) -> None:
    written = scaffold((), root=tmp_path)

    assert len(written) == len(NAMES)
    assert (tmp_path / "playgrounds" / "__init__.py").is_file()

    for name in NAMES:
        copied = tmp_path / "playgrounds" / f"{name}.py"
        assert copied.read_text(encoding="utf-8") == (SHIPPED / f"{name}.py").read_text("utf-8")


def test_scaffold_scopes_to_requested_names(tmp_path: Path) -> None:
    scaffold(("counter",), root=tmp_path)
    present = sorted(p.name for p in (tmp_path / "playgrounds").glob("*.py"))

    assert present == ["__init__.py", "counter.py"]


def test_rerunning_replaces_edits(tmp_path: Path) -> None:
    scaffold(("counter",), root=tmp_path)
    edited = tmp_path / "playgrounds" / "counter.py"
    edited.write_text("broken = True\n", encoding="utf-8")
    scaffold(("counter",), root=tmp_path)

    assert edited.read_text(encoding="utf-8") == (SHIPPED / "counter.py").read_text("utf-8")


def test_scaffold_wipes_only_the_requested_playgrounds_state(tmp_path: Path) -> None:
    scaffold((), root=tmp_path)
    state = tmp_path / "playgrounds" / ".state"

    for name in ("counter", "durable"):
        (state / name).mkdir(parents=True)
        (state / name / "store").write_text("kept", encoding="utf-8")
    scaffold(("counter",), root=tmp_path)

    assert not (state / "counter").exists()
    assert (state / "durable" / "store").read_text(encoding="utf-8") == "kept"

    scaffold((), root=tmp_path)

    assert not (state / "durable").exists()


def test_unknown_reports_only_unknown_names() -> None:
    assert unknown(("counter", "nope", "durable")) == ("nope",)
    assert unknown(NAMES) == ()


@pytest.mark.parametrize("name", NAMES)
def test_scaffolded_source_imports_and_exposes_an_app(tmp_path: Path, name: str) -> None:
    scaffold((name,), root=tmp_path)
    probe = (
        "import sys; sys.path.insert(0, '.');"
        f"import playgrounds.{name} as m; assert hasattr(m, 'app'); print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=tmp_path, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def _run_cli(tmp_path: Path, *names: str) -> subprocess.CompletedProcess[str]:
    probe = "from pysx.tooling.playground import main; main()"

    return subprocess.run(
        [sys.executable, "-c", probe, *names],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_rejects_an_unknown_playground(tmp_path: Path) -> None:
    result = _run_cli(tmp_path, "nope")

    assert result.returncode != 0
    assert "unknown playground: nope" in result.stderr


def test_cli_writes_every_playground_by_default(tmp_path: Path) -> None:
    result = _run_cli(tmp_path)

    assert result.returncode == 0, result.stderr

    for name in NAMES:
        assert f"wrote playgrounds/{name}.py" in result.stdout
        assert (tmp_path / "playgrounds" / f"{name}.py").is_file()


def test_console_script_is_registered() -> None:
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'pysx-playground = "pysx.tooling.playground:main"' in config
