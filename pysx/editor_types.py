"""Owned strict diagnostics for an immutable editor namespace, without app imports."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast


def diagnostics(directory: Path, root: Path) -> dict[str, object]:
    directory, root = directory.resolve(), root.resolve()

    if directory.parent != root or not directory.name.startswith("_pysx_revision_"):

        raise ValueError("diagnostics require an owned workspace revision")
    files = sorted(directory.rglob("*.py"))

    if not files or len(files) > 513:

        raise ValueError("invalid diagnostic input coverage")
    original_json = root / "pyrightconfig.json"
    original_toml = root / "pyproject.toml"
    config: dict[str, object] = {}

    if original_json.is_file():
        config["extends"] = str(original_json)
    elif original_toml.is_file():
        with original_toml.open("rb") as stream:
            config.update(tomllib.load(stream).get("tool", {}).get("pyright", {}))
    config.update(
        include=[directory.name],
        exclude=[],
        ignore=[],
        typeCheckingMode="strict",
        reportUnusedFunction="error",
    )
    configuration = root / f"{directory.name}.json"

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
                *map(str, files),
            ],
            cwd=root,
            capture_output=True,
            text=True,
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
