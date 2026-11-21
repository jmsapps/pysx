"""Owned strict diagnostics for an immutable editor namespace, without app imports."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

from pysx.tooling.lsp.workspace import project_settings


def owned_revision(directory: Path, root: Path) -> bool:
    """A projection this extension created, in managed state or the legacy location.

    The name prefix alone is not enough: the directory must also sit somewhere the
    extension owns, so an arbitrary path cannot be handed in.
    """

    if not directory.name.startswith("_pysx_revision_"):
        return False

    if directory.parent == root:
        return True
    from pysx.runtime.state_paths import state_root

    return directory.is_relative_to(state_root(start=root).resolve())


def diagnostics(directory: Path, root: Path) -> dict[str, object]:
    directory, root = directory.resolve(), root.resolve()

    if not owned_revision(directory, root):
        raise ValueError("diagnostics require an owned workspace revision")
    files = sorted(directory.rglob("*.py"))

    if not files or len(files) > 513:
        raise ValueError("invalid diagnostic input coverage")
    original_json = root / "pyrightconfig.json"
    original_toml = root / "pyproject.toml"
    config = project_settings(original_json if original_json.is_file() else original_toml)
    config.update(
        # Scope defaults below the hidden state root, not above it.
        include=[directory.name],
        exclude=[],
        ignore=[],
        typeCheckingMode="strict",
        reportUnusedFunction="error",
    )
    # The configuration is managed state too, so it stays beside the projection rather
    # than in the user's project.
    configuration = directory.parent / f"{directory.name}.json"

    if configuration.exists():
        raise ValueError("diagnostic configuration already exists")

    try:
        with configuration.open("x", encoding="utf-8") as stream:
            json.dump(config, stream)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pyright",
                "--outputjson",
                "--pythonpath",
                sys.executable,
                "--project",
                str(configuration),
                *(str(file.relative_to(directory.parent)) for file in files),
            ],
            cwd=directory.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=25,
        )

        if result.returncode not in {0, 1}:
            raise RuntimeError(result.stderr or "Pyright editor analysis failed")
        payload = cast("dict[str, object]", json.loads(result.stdout))
        summary = cast("dict[str, int]", payload["summary"])

        if summary["filesAnalyzed"] < len(files):
            raise RuntimeError("Pyright skipped editor projection inputs")

        return payload
    finally:
        configuration.unlink(missing_ok=True)


def main() -> None:
    sys.stdout.write(json.dumps(diagnostics(Path(sys.argv[1]), Path(sys.argv[2]))) + "\n")


if __name__ == "__main__":
    main()
