"""Optional resident language server entry point: ``python -m pysx.lsp``."""

from __future__ import annotations

import importlib.util
import logging
import sys


def main() -> None:
    missing = [
        name for name in ("pygls", "pyright", "ruff") if importlib.util.find_spec(name) is None
    ]

    if missing:
        sys.stderr.write("Language server requires pysx[lsp] in the selected Python environment.\n")

        raise SystemExit(2)
    from .lsp_server import create_server

    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    create_server().start_io()


if __name__ == "__main__":
    main()
