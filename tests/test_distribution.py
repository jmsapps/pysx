"""Fresh wheel and sdist consumer evidence for the isolated packaging candidate."""

from pathlib import Path

from prototypes.distribution import qualify


def test_fresh_installed_distribution(tmp_path: Path) -> None:
    report = qualify(tmp_path)
    assert Path(report["wheel"]).is_file()
    assert Path(report["sdist_wheel"]).is_file()
    assert Path(report["vsix"]).is_file()
