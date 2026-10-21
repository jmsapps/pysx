"""Encoding-aware input and maintained-source discovery shared by tooling."""

from __future__ import annotations

import tokenize
from pathlib import Path  # noqa: TC003 - public source API annotations resolve at runtime

EXCLUDED = frozenset({"node_modules", "__pycache__", "build", "dist"})


def source_files(root: Path) -> tuple[Path, ...]:
    """Match the maintained tree; hidden/vendor/cache directories never become inputs."""

    return tuple(
        sorted(
            path
            for path in root.rglob("*.py")
            if not any(
                part.startswith((".", "_pysx_revision_")) or part in EXCLUDED
                for part in path.relative_to(root).parts
            )
        )
    )


def read_source(path: Path) -> str:
    with path.open("rb") as stream:
        encoding, _ = tokenize.detect_encoding(stream.readline)
        stream.seek(0)

        return stream.read().decode(encoding)
