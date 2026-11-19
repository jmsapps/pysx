"""Release synchronization preserves unrelated versions and historical entries."""

import shutil
import subprocess
import sys
import tomllib
from typing import TYPE_CHECKING

from scripts.sync_version import DOCUMENTS, ROOT, updates
from shared import RELEASE_VERSION

if TYPE_CHECKING:
    from pathlib import Path


def test_release_version_synchronizes_metadata_and_preserves_dependencies(tmp_path: Path) -> None:
    names = (
        "pyproject.toml",
        "tutorial/pyproject.toml",
        "editor/package.json",
        "editor/package-lock.json",
        *DOCUMENTS,
    )

    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    original = tomllib.loads((tmp_path / "pyproject.toml").read_text())
    changed = updates(tmp_path, "9.8.7")

    for path, source in changed.items():
        path.write_text(source)
    project = tomllib.loads((tmp_path / "pyproject.toml").read_text())
    assert project["project"]["version"] == "9.8.7"
    assert project["project"]["dependencies"] == original["project"]["dependencies"]
    assert project["build-system"] == original["build-system"]
    assert (
        "pysx_framework-9.8.7-py3-none-any.whl"
        in (tmp_path / "tutorial/pyproject.toml").read_text()
    )
    assert "pysx-framework==9.8.7" in (tmp_path / "README.md").read_text()
    assert not updates(tmp_path, "9.8.7")


def test_release_version_metadata_matches_constant() -> None:
    assert not updates(ROOT, RELEASE_VERSION)


def test_release_version_tutorial_bootstrap_works_before_install(tmp_path: Path) -> None:
    shutil.copy2(ROOT / "tutorial/bootstrap.py", tmp_path)
    shutil.copy2(ROOT / "tutorial/pyproject.toml", tmp_path)
    result = subprocess.run(
        [sys.executable, "-I", str(tmp_path / "bootstrap.py"), "--wheel", "missing.whl"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert f"Expected the actual pysx {RELEASE_VERSION} candidate wheel" in result.stderr


def test_package_description_release_links_follow_the_version(tmp_path: Path) -> None:
    """A released description must document its own tag, not a frozen earlier one."""
    shutil.copytree(
        ROOT,
        tmp_path / "checkout",
        dirs_exist_ok=True,
        symlinks=True,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "node_modules", "dist", ".pysx", "__pycache__"
        ),
    )
    checkout = tmp_path / "checkout"
    readme = checkout / "README.md"
    assert f"/blob/v{RELEASE_VERSION}/" in readme.read_text()

    changed = updates(checkout, "9.9.9")
    assert readme in changed, "the release-tagged links are synchronized"
    updated = changed[readme]
    assert "/blob/v9.9.9/" in updated
    assert f"/blob/v{RELEASE_VERSION}/" not in updated
    # Only the tag moves; the documentation targets themselves are untouched.
    assert updated.count("/blob/v9.9.9/") == readme.read_text().count(f"/blob/v{RELEASE_VERSION}/")


def test_package_description_uses_public_absolute_targets() -> None:
    """The long description is the README verbatim, so its targets must be public."""
    import re

    text = (ROOT / "README.md").read_text()
    relative = re.findall(r"\]\((?!https?://|#)[^)]+\)|src=\"(?!https?://)[^\"]+\"", text)
    assert not relative, relative
    assert 'src="https://jmsapps.ca/assets/pysx-logo.png"' in text
    assert 'src="https://jmsapps.ca/assets/pysx-counter.svg"' in text
