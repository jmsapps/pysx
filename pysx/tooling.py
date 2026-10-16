"""Resolve the developer tooling pysx drives, from the selected environment only.

An editor and the CLI must agree on one Ruff, and that Ruff must be the one
installed beside the selected interpreter. Silently falling back to whatever
`ruff` happens to be on PATH is how a workspace ends up linted by a different
version than the one its checks were qualified against.
"""

from __future__ import annotations

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


def _candidates(interpreter: Path) -> list[Path]:
    """Only locations owned by the selected interpreter; never the running one."""

    if sys.platform == "win32":
        roots = [interpreter.parent, interpreter.parent / "Scripts"]
        names = ("ruff.exe", "ruff")
    else:
        roots = [interpreter.parent]
        names = ("ruff",)

    return [root / name for root in roots for name in names]


def resolve_ruff(interpreter: Path | None = None) -> Tool:
    """Find Ruff beside the selected interpreter and reject any other version."""
    selected = Path(interpreter or sys.executable)
    found = next((path for path in _candidates(selected) if path.is_file()), None)

    if found is None:
        raise ToolingError(
            f"Ruff {RUFF_REQUIREMENT} is not installed in {selected.parent}. "
            f"Install it with `pip install 'pysx[dev]'` into that environment. "
            "pysx does not fall back to a Ruff found elsewhere on PATH."
        )

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
            f"{found} is Ruff {version}, but this pysx requires {RUFF_REQUIREMENT}. "
            "Install the pinned version into the selected environment; pysx will not "
            "use a different one."
        )

    return Tool("ruff", found, version)


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
