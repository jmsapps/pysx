"""Install or remove the published VSCode extension through VSCode's own CLI.

The Python distribution carries no VSIX and no repository scripts. These commands name
the published identifier and let VSCode choose the version, manage its registry and
decide where the extension lives. Nothing here imports or starts an application.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

EXTENSION_ID = "jmsapps.pysx"
TIMEOUT_SECONDS = 300
_GUIDANCE = (
    "VSCode's `code` command was not found. Open VSCode and run "
    "\"Shell Command: Install 'code' command in PATH\" from the Command Palette, "
    "or set PYSX_VSCODE_CLI to the full path of the `code` executable, then try again."
)


def _candidates() -> list[Path]:
    """Explicit locations only, so a stray script named `code` cannot be invoked."""
    supplied = os.environ.get("PYSX_VSCODE_CLI")

    if supplied:
        return [Path(supplied)]
    found = shutil.which("code")
    located = [Path(found)] if found else []

    if sys.platform == "darwin":
        located.append(Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"))
    elif sys.platform == "win32":
        # The Windows CLI is a shim beside the executable; `code` alone is a .cmd.
        local = os.environ.get("LOCALAPPDATA")

        if local:
            located.append(Path(local) / "Programs/Microsoft VS Code/bin/code.cmd")
    else:
        located.extend([Path("/usr/share/code/bin/code"), Path("/usr/bin/code")])

    return located


def resolve_cli() -> Path:
    found = next((path for path in _candidates() if path.is_file()), None)

    if found is None:
        raise SystemExit(_GUIDANCE)

    return found


def run(operation: str, *, extension: str = EXTENSION_ID) -> int:
    """Invoke VSCode's CLI with structured arguments and surface its own output."""

    if operation not in {"install", "uninstall"}:
        raise ValueError("operation must be install or uninstall")
    cli = resolve_cli()
    arguments = [str(cli), f"--{operation}-extension", extension]

    for name in ("PYSX_VSCODE_EXTENSIONS_DIR", "PYSX_VSCODE_USER_DATA_DIR"):
        value = os.environ.get(name)

        if value:
            flag = "--extensions-dir" if name.endswith("EXTENSIONS_DIR") else "--user-data-dir"
            arguments.extend([flag, value])

    try:
        result = subprocess.run(
            arguments, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False
        )
    except OSError as error:
        raise SystemExit(f"{cli} could not be run: {error}") from error
    except subprocess.TimeoutExpired:
        raise SystemExit(f"{cli} did not finish within {TIMEOUT_SECONDS} seconds") from None
    output = (result.stdout + result.stderr).strip()

    if output:
        print(output)

    # Success is claimed only when VSCode reports it; a Marketplace or network failure
    # exits nonzero here rather than printing a cheerful message.
    if result.returncode != 0:
        verb = "install" if operation == "install" else "uninstall"
        print(f"pysx: could not {verb} {extension}.", file=sys.stderr)

        return result.returncode
    print(f"pysx: {extension} {'installed' if operation == 'install' else 'uninstalled'}.")

    return 0
