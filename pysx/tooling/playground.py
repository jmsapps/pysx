"""Copy runnable playground applications into a project directory."""

from __future__ import annotations

import argparse
import shutil
from importlib.resources import files
from pathlib import Path

from pysx.playgrounds import NAMES

DIRECTORY = "playgrounds"
STATE = ".state"


def unknown(requested: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name for name in requested if name not in NAMES)


def scaffold(requested: tuple[str, ...], *, root: Path) -> tuple[Path, ...]:
    """Write the requested playgrounds into root, replacing any existing copies."""
    selected = requested or NAMES
    target = root / DIRECTORY
    target.mkdir(parents=True, exist_ok=True)
    (target / "__init__.py").write_text("", encoding="utf-8")
    written: list[Path] = []

    for name in selected:
        source = files("pysx").joinpath(f"playgrounds/{name}.py").read_text(encoding="utf-8")
        destination = target / f"{name}.py"
        destination.write_text(source, encoding="utf-8")
        written.append(destination)
        shutil.rmtree(target / STATE / name, ignore_errors=True)

    return tuple(written)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pysx-playground",
        description="Copy runnable playground applications into ./playgrounds.",
    )
    parser.add_argument(
        "names",
        nargs="*",
        metavar="NAME",
        help=f"playgrounds to write; all of them by default ({', '.join(NAMES)})",
    )
    args = parser.parse_args()
    requested = tuple(args.names)
    missing = unknown(requested)

    if missing:
        parser.error("unknown playground: " + ", ".join(missing))
    root = Path.cwd()

    for path in scaffold(requested, root=root):
        print(f"wrote {path.relative_to(root)}")
    print("run one with: pysx --app playgrounds.<name>")
