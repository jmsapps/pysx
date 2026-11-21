"""Resolve the developer tooling pysx drives, from the selected environment only.

An editor and the CLI must agree on one Ruff, and that Ruff must be one this
project resolved deliberately: either the copy installed beside the selected
interpreter, or an explicitly supplied managed location. Silently falling back to
whatever `ruff` happens to be on PATH is how a workspace ends up linted by a
different version than the one its checks were qualified against, so PATH is never
consulted. The editor supplies its extension-owned closure through
`PYSX_MANAGED_TOOLING`; with that unset the behaviour is unchanged.

Qualification is memoized per binary and invalidated by its size and modification
time, because linting a workspace resolves the tool once per analyzed source and a
version probe per call dominates the gate. Only a qualified result is remembered, so
installing the pinned Ruff into an incomplete environment needs no restart.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

RUFF_REQUIREMENT = "0.16.10"
_VERSION = re.compile(r"^ruff\s+(\d+\.\d+\.\d+)")


class ToolingError(RuntimeError):
    """Raised with actionable guidance when the pinned tooling is unusable."""


@dataclass(frozen=True)
class Tool:
    name: str
    path: Path
    version: str


_qualified: dict[Path, tuple[tuple[int, int], Tool]] = {}


def managed_root() -> Path | None:
    """The editor's extension-owned tooling closure, when one was supplied."""
    location = os.environ.get("PYSX_MANAGED_TOOLING")

    return Path(location) if location else None


def _binary_roots(base: Path) -> list[Path]:
    return [base, base / "Scripts"] if sys.platform == "win32" else [base / "bin", base]


def _candidates(interpreter: Path, managed: Path | None) -> list[Path]:
    """Explicit locations only: the managed closure first, then the selected environment."""
    names = ("ruff.exe", "ruff") if sys.platform == "win32" else ("ruff",)
    roots: list[Path] = []

    if managed is not None:
        roots.extend(_binary_roots(managed))
    roots.extend(
        [interpreter.parent, interpreter.parent / "Scripts"]
        if sys.platform == "win32"
        else [interpreter.parent]
    )

    return [root / name for root in roots for name in names]


def resolve_ruff(interpreter: Path | None = None, location: Path | None = None) -> Tool:
    """Find Ruff in the supplied location or beside the interpreter; reject other versions."""
    selected = Path(interpreter or sys.executable)
    managed = location if location is not None else managed_root()
    found = next((path for path in _candidates(selected, managed) if path.is_file()), None)

    if found is None:
        where = f"{managed} or {selected.parent}" if managed is not None else str(selected.parent)

        raise ToolingError(
            f"Ruff {RUFF_REQUIREMENT} is not installed in {where}. "
            f"Install it with `pip install 'pysx-framework[dev]'` into that environment, "
            "or let the editor provision its own tooling. "
            "PYSX does not fall back to a Ruff found elsewhere on PATH."
        )

    binary = found.stat()
    stamp = (binary.st_mtime_ns, binary.st_size)
    remembered = _qualified.get(found)

    if remembered is not None and remembered[0] == stamp:
        return remembered[1]

    try:
        result = subprocess.run(
            [str(found), "--version"], capture_output=True, text=True, timeout=30, check=True
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ToolingError(f"{found} could not be run: {error}") from error
    match = _VERSION.match(result.stdout.strip())

    if match is None:
        raise ToolingError(f"{found} did not report a Ruff version: {result.stdout.strip()!r}")
    version = match.group(1)

    if version != RUFF_REQUIREMENT:
        raise ToolingError(
            f"{found} is Ruff {version}, but this PYSX requires {RUFF_REQUIREMENT}. "
            "Install the pinned version into the selected environment; PYSX will not "
            "use a different one."
        )

    tool = Tool("ruff", found, version)
    _qualified[found] = (stamp, tool)

    return tool


def main() -> None:
    import json

    try:
        tool = resolve_ruff(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    except ToolingError as error:
        sys.stdout.write(json.dumps({"error": str(error)}) + "\n")
        sys.exit(1)
    sys.stdout.write(
        json.dumps({"name": tool.name, "path": str(tool.path), "version": tool.version}) + "\n"
    )


if __name__ == "__main__":
    main()
