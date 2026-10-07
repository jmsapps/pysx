"""The verification gate manifest. CI and local runs execute this same list.

Adding a gate here is the only supported way to add one: `tests/test_verification_manifest.py`
fails if `.github/workflows/quality.yml` grows a step that this file does not declare.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON_VERSION = "3.14.4"
BROWSER_ENGINES = ("chromium", "firefox", "webkit")

BOOTSTRAP: tuple[tuple[str, ...], ...] = (
    ("uv", "python", "install", PYTHON_VERSION),
    ("uv", "sync", "--locked", "--python", PYTHON_VERSION),
)

ENTRYPOINT: tuple[str, ...] = ("uv", "run", "--project", ".", "python", "scripts/verify.py", "all")


@dataclass(frozen=True)
class Gate:
    tier: str
    name: str
    command: tuple[str, ...]
    needs_display: bool = False


def _playwright_install() -> tuple[str, ...]:
    flags = ("--with-deps",) if sys.platform.startswith("linux") else ()

    install = ("npm", "--prefix", "tests", "exec", "--", "playwright", "install")

    return (*install, *flags, *BROWSER_ENGINES)


GATES: tuple[Gate, ...] = (
    Gate("setup", "npm-tests", ("npm", "--prefix", "tests", "ci")),
    Gate("setup", "npm-editor", ("npm", "--prefix", "editor", "ci")),
    Gate("setup", "playwright", _playwright_install()),
    Gate("setup", "vscode-host", ("npm", "--prefix", "editor", "run", "setup:host")),
    Gate("python", "lockfile", ("uv", "lock", "--check")),
    Gate("python", "ruff", ("uv", "run", "--project", ".", "python", "-m", "pysx.quality", "ruff")),
    Gate("python", "mypy", ("uv", "run", "--project", ".", "python", "-m", "pysx.quality", "mypy")),
    Gate(
        "python",
        "pyright",
        ("uv", "run", "--project", ".", "python", "-m", "pysx.quality", "pyright"),
    ),
    Gate(
        "python",
        "generator",
        ("uv", "run", "--project", ".", "python", "scripts/generate_native.py", "--check"),
    ),
    Gate("python", "pytest", ("uv", "run", "--project", ".", "pytest", "-q")),
    Gate("browser", "browser", ("npm", "--prefix", "tests", "run", "browser")),
    Gate("grammar", "grammar", ("npm", "--prefix", "tests", "run", "grammar")),
    Gate("editor", "editor", ("npm", "--prefix", "editor", "test"), needs_display=True),
)

TIERS: tuple[str, ...] = ("setup", "python", "browser", "grammar", "editor")
SELECTORS: dict[str, tuple[str, ...]] = {
    "all": TIERS,
    "gates": tuple(tier for tier in TIERS if tier != "setup"),
    **{tier: (tier,) for tier in TIERS},
}


def environment() -> dict[str, str]:
    env = dict(os.environ)
    managed = ROOT / ".vscode-test" / "extensions"

    if "PYSX_EXTENSIONS_DIR" not in env and managed.is_dir():
        env["PYSX_EXTENSIONS_DIR"] = str(managed)

    return env


def resolve(gate: Gate) -> tuple[str, ...]:
    needs_xvfb = (
        gate.needs_display
        and sys.platform.startswith("linux")
        and not os.environ.get("DISPLAY")
        and shutil.which("xvfb-run") is not None
    )

    return ("xvfb-run", "-a", *gate.command) if needs_xvfb else gate.command


def run(gate: Gate, *, stream: bool) -> tuple[bool, float, str]:
    command = resolve(gate)
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment(),
        check=False,
        capture_output=not stream,
        text=True,
    )
    elapsed = time.monotonic() - started
    output = "" if stream else f"{result.stdout}{result.stderr}"

    return result.returncode == 0, elapsed, output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selector", nargs="?", default="gates", choices=sorted(SELECTORS))
    parser.add_argument("--verbose", action="store_true", help="stream gate output as it runs")
    parser.add_argument("--list", action="store_true", help="print the selected gates and exit")
    args = parser.parse_args()
    tiers = SELECTORS[args.selector]
    selected = [gate for gate in GATES if gate.tier in tiers]

    if args.list:
        for gate in selected:
            print(f"{gate.tier:8} {gate.name:12} {' '.join(resolve(gate))}")

        return 0
    failures: list[tuple[Gate, str]] = []

    for gate in selected:
        print(f"-> {gate.tier}/{gate.name}", flush=True)
        passed, elapsed, output = run(gate, stream=args.verbose)

        if passed:
            print(f"   ok {gate.name} ({elapsed:.1f}s)", flush=True)

            continue
        print(f"   FAILED {gate.name} ({elapsed:.1f}s): {' '.join(resolve(gate))}", flush=True)

        if output:
            print(output, flush=True)
        failures.append((gate, output))

        if gate.tier == "setup":

            break

    if failures:
        names = ", ".join(gate.name for gate, _output in failures)
        print(f"VERIFICATION FAILED: {len(failures)} gate(s): {names}", flush=True)

        return 1
    print(f"VERIFICATION PASSED: {len(selected)} gates ({args.selector})", flush=True)

    return 0


if __name__ == "__main__":

    raise SystemExit(main())
