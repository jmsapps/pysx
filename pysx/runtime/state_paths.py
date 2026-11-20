"""One place that decides where PYSX-managed application state lives.

Hosts, editor processes and framework-managed examples all resolve through here, so a
single application has one state location regardless of which process or working
directory happens to reach it. Three kinds of state are deliberately outside this model:
extension-wide editor tooling, paths an application selects explicitly, and external
tools' own caches.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import re
from functools import partial
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

    return owning(base) or (base.parent if base.is_file() else base)


def application_source(app: object) -> Path | None:
    """The defining source owns implicit state, including wrapped callables."""
    from pysx.compiler.projects import project_root as owning

    target = app

    while isinstance(target, partial):
        target = target.func

    if inspect.ismethod(target):
        target = target.__func__

    if not inspect.isfunction(target):
        target = type(target)
    target = inspect.unwrap(target)

    try:
        filename = inspect.getsourcefile(target)
    except TypeError:
        return None

    if filename:
        source = Path(filename).absolute()

        if source.is_file() and owning(source) is not None:
            return source

    return None


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
    """Migrate recovery material through its platform storage owner."""
    from pysx.hosting.recovery_keys import KEY_FILES, migrate_keys

    if names != KEY_FILES:
        raise ValueError("only recovery-key migration is supported")

    return migrate_keys(legacy, destination)
