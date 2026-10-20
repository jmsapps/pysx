"""Bounded one-shot static analysis worker; never imports application code."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .check import diagnostics_for_source
from .tracing import source_link

if TYPE_CHECKING:
    from .wire import JSONValue


def analyse(paths: list[str], roots: tuple[Path, ...]) -> dict[str, JSONValue]:
    if len(paths) > 1024:
        raise ValueError("source analysis exceeds 1024 files")
    records: list[JSONValue] = []
    size = 0

    for filename in paths:
        path = Path(filename).resolve()

        if path.suffix != ".py" or not any(path.is_relative_to(root) for root in roots):
            continue

        try:
            body = path.read_bytes()
        except FileNotFoundError:
            continue
        size += len(body)

        if len(body) > 4 * 1024 * 1024 or size > 16 * 1024 * 1024:
            raise ValueError("source analysis exceeds its byte limit")
        source = body.decode("utf-8")
        diagnostics = diagnostics_for_source(source, str(path), workspace_roots=roots)

        if len(diagnostics) > 256:
            raise ValueError("source analysis exceeds 256 diagnostics per file")
        records.append(
            {
                "path": str(path),
                "digest": hashlib.sha256(body).hexdigest(),
                "diagnostics": cast("list[JSONValue]", diagnostics),
                "source": cast("JSONValue", source_link(path, 1, roots)),
            }
        )

    return {"files": records}


def main() -> None:
    try:
        data: object = json.loads(sys.stdin.buffer.read(16 * 1024 * 1024 + 1))

        if not isinstance(data, list):
            raise ValueError("expected source paths")
        items = cast("list[object]", data)

        if not all(isinstance(path, str) for path in items):
            raise ValueError("expected source paths")
        roots = tuple(Path(path).resolve() for path in json.loads(os.environ["PYSX_DEV_ROOTS"]))
        result = analyse(cast("list[str]", data), roots)
        output = json.dumps(result).encode()

        if len(output) > 16 * 1024 * 1024:
            raise ValueError("analysis output exceeds 16 MiB")
    except Exception as error:
        output = json.dumps({"error": f"{type(error).__name__}: {error}"[-2000:]}).encode()
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()
