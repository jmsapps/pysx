"""Build the production .vsix with the pinned vsce packager.

The Marketplace gallery validates the package manifest that `code
--install-extension` ignores, so packaging is delegated to the official tool
rather than hand-rolled. One artifact is produced for sideloading, the editor
suites and the gallery upload alike.

vsce reads packaging inputs from the extension directory only, so the shared
repository LICENSE is staged there for the duration of the build and removed
again; the checkout keeps a single copy.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Generator

HERE = Path(__file__).parent
ROOT = HERE.parent
VSCE = HERE / "node_modules" / "@vscode" / "vsce" / "vsce"
NODE = shutil.which("node") or "node"

raw_package: object = json.loads((HERE / "package.json").read_text())

if not isinstance(raw_package, dict):
    raise TypeError("extension manifest must be an object")
package = cast("dict[str, object]", raw_package)
PKG: dict[str, str] = {}

for key in ("name", "version"):
    value = package.get(key)

    if not isinstance(value, str):
        raise TypeError(f"extension manifest {key!r} must be a string")
    PKG[key] = value

DEFAULT_NAME = f"{PKG['name']}-{PKG['version']}.vsix"
STAGED = ((ROOT / "LICENSE", HERE / "LICENSE"),)


@contextmanager
def staged() -> Generator[None]:
    written: list[Path] = []
    try:
        for source, destination in STAGED:
            # An interrupted build leaves its own copy behind; anything else
            # under that name is somebody's file and must not be consumed.
            if destination.exists() and destination.read_bytes() != source.read_bytes():
                raise FileExistsError(f"{destination} would be overwritten by the build")
            shutil.copyfile(source, destination)
            written.append(destination)
        yield
    finally:
        for destination in written:
            destination.unlink(missing_ok=True)


def build(out: Path) -> Path:
    if not VSCE.is_file():
        raise FileNotFoundError(f"{VSCE} is missing; run npm --prefix editor ci")

    if out.is_dir():
        out = out / DEFAULT_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)

    with staged():
        subprocess.run(
            [NODE, str(VSCE), "package", "--out", str(out)],
            cwd=HERE,
            check=True,
            capture_output=True,
            text=True,
        )

    if not out.is_file():
        raise FileNotFoundError(f"vsce reported success but {out} is missing")

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=HERE / DEFAULT_NAME,
        help="file, or directory to name it in; rebuilt from scratch on every run",
    )
    print(build(parser.parse_args().out.resolve()))


if __name__ == "__main__":
    main()
