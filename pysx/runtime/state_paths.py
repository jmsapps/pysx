"""One place that decides where PYSX-managed application state lives.

Hosts, editor processes and framework-managed examples all resolve through here, so a
single application has one state location regardless of which process or working
directory happens to reach it. Three kinds of state are deliberately outside this model:
extension-wide editor tooling, paths an application selects explicitly, and external
tools' own caches.
"""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import suppress
from pathlib import Path

MARKER = ".pysx"
IGNORE = "*\n"
PERSISTENT = "persistent"
TEMPORARY = "temporary"
ENVIRONMENT = "PYSX_STATE_DIR"
_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_COMPONENT = 64


def component(namespace: str) -> str:
    """A readable, filesystem-safe directory name that cannot escape its parent."""

    if not namespace:
        raise ValueError("state namespace required")
    cleaned = _SAFE.sub("-", namespace).strip(".-")

    if not cleaned or len(cleaned) > _MAX_COMPONENT or cleaned != namespace:
        # Anything adjusted is also digested, so two namespaces cannot collide after
        # sanitising and a long or exotic identity still gets a stable name.
        digest = hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:16]
        prefix = cleaned[:32].strip(".-")

        return f"{prefix}-{digest}" if prefix else digest

    return cleaned


def project_root(start: Path | None = None) -> Path:
    """The owning project, discovered the same way analysis discovers it."""
    from pysx.compiler.projects import project_root as owning

    base = (start or Path.cwd()).absolute()

    return owning(base) or base


def state_root(directory: Path | None = None, *, start: Path | None = None) -> Path:
    """Explicit application root, else the environment, else the project's `.pysx/`."""

    if directory is not None:
        return Path(directory).absolute()
    configured = os.environ.get(ENVIRONMENT)

    if configured:
        return Path(configured).absolute()

    return project_root(start) / MARKER


def ensure_root(root: Path) -> Path:
    """Create the state root and make it ignore itself, never the user's `.gitignore`."""
    root.mkdir(parents=True, exist_ok=True)

    if root.name == MARKER:
        ignore = root / ".gitignore"

        if not ignore.exists():
            ignore.write_text(IGNORE, encoding="utf-8")

    return root


def persistent_path(
    namespace: str, *, directory: Path | None = None, start: Path | None = None
) -> Path:
    """Durable application state: recovery keys and framework-managed stores."""
    root = ensure_root(state_root(directory, start=start))
    path = root / component(namespace) / PERSISTENT
    path.mkdir(parents=True, exist_ok=True)

    return path


def temporary_path(
    namespace: str, owner: str, *, directory: Path | None = None, start: Path | None = None
) -> Path:
    """Disposable analysis output. Only its owner may remove it."""
    root = ensure_root(state_root(directory, start=start))
    path = root / component(namespace) / TEMPORARY / component(owner)
    path.mkdir(parents=True, exist_ok=True)

    return path


class StateConflictError(RuntimeError):
    """Raised when migration would overwrite or merge unrelated application state."""


def migrate(legacy: Path, destination: Path, *, names: tuple[str, ...]) -> tuple[str, ...]:
    """Copy implicit legacy defaults forward without changing or destroying anything.

    The legacy source is retained, an identical destination is a no-op, and differing
    data at the destination fails with an actionable error instead of being merged.
    """

    if not legacy.is_dir() or legacy.resolve() == destination.resolve():
        return ()
    moved: list[str] = []

    for name in names:
        source = legacy / name

        if not source.is_file():
            continue
        target = destination / name

        if target.is_file():
            if target.read_bytes() != source.read_bytes():
                raise StateConflictError(
                    f"{target} already holds different data than the legacy {source}. "
                    "Choose one explicitly with HostConfig.state_directory or "
                    "recovery_key_directory, then remove the other."
                )

            continue
        destination.mkdir(parents=True, exist_ok=True)
        # Publish atomically so an interrupted migration never leaves a partial key.
        staging = target.with_name(f".{target.name}.incoming")
        staging.write_bytes(source.read_bytes())
        staging.replace(target)

        with suppress(OSError):
            target.chmod(source.stat().st_mode & 0o777)
        moved.append(name)

    return tuple(moved)
