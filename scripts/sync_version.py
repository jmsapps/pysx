"""Synchronize release metadata and installation docs from shared.constants."""

import argparse
import re
import subprocess
import sys
from pathlib import Path

from shared import RELEASE_VERSION

ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = (
    "README.md",
    "editor/README.md",
    "docs/QUICKSTART.md",
    "docs/RELEASING.md",
    "tutorial/README.md",
)


def updates(root: Path, version: str) -> dict[Path, str]:
    """Return changed files without altering dependency versions or release history."""

    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise ValueError("RELEASE_VERSION must be a stable major.minor.patch version")
    result: dict[Path, str] = {}

    for name in ("pyproject.toml", "tutorial/pyproject.toml", *DOCUMENTS):
        path = root / name
        original = path.read_text()
        source = original

        if name.endswith("pyproject.toml"):
            source = re.sub(
                r'^version = "[^"]+"', f'version = "{version}"', source, count=1, flags=re.M
            )
        source = re.sub(
            r"(pysx-framework(?:\[[^\]]+\])?==|pysx_framework-)\d+\.\d+\.\d+",
            lambda match: f"{match[1]}{version}",
            source,
        )

        if source != original:
            result[path] = source

    for name, count in (("editor/package.json", 1), ("editor/package-lock.json", 2)):
        path = root / name
        original = path.read_text()
        source = re.sub(r'"version": "[^"]+"', f'"version": "{version}"', original, count=count)

        if source != original:
            result[path] = source

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="check metadata without writing or building"
    )
    args = parser.parse_args()
    pending = updates(ROOT, RELEASE_VERSION)

    if args.check:
        if pending:
            raise SystemExit(
                "Release version is out of sync: "
                + ", ".join(str(path.relative_to(ROOT)) for path in pending)
            )
        sys.stdout.write(f"Release metadata matches {RELEASE_VERSION}\n")

        return

    for path, source in pending.items():
        path.write_text(source)

    # Rebuild before locking: the tutorial records the actual candidate wheel hash.
    for command in (
        ["uv", "lock"],
        ["uv", "build", "--wheel", "--out-dir", "tutorial/.artifacts"],
        ["uv", "lock", "--project", "tutorial", "--upgrade-package", "pysx-framework"],
    ):
        subprocess.run(command, cwd=ROOT, check=True)
    sys.stdout.write(f"Synchronized release {RELEASE_VERSION}\n")


if __name__ == "__main__":
    main()
