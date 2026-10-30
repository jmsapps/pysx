"""Prepare the independent tutorial environment from an actual framework wheel."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, help="use an existing framework candidate wheel")
    parser.add_argument(
        "--editor", action="store_true", help="include pinned language-server extras"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    artifacts = root / ".artifacts"
    artifacts.mkdir(exist_ok=True)
    uv = shutil.which("uv")

    if uv is None:
        raise SystemExit("Install uv before preparing the tutorial environment")

    if args.wheel is not None:
        wheel = args.wheel.resolve()

        if wheel.name != "pysx-0.1.0-py3-none-any.whl" or not wheel.is_file():
            raise SystemExit("Expected the actual pysx 0.1.0 candidate wheel")
        target = artifacts / wheel.name

        if wheel != target:
            shutil.copy2(wheel, target)
    else:
        subprocess.run(
            [
                uv,
                "build",
                str(root.parent),
                "--wheel",
                "--out-dir",
                str(artifacts),
                "--python",
                sys.executable,
            ],
            check=True,
        )
    subprocess.run([uv, "lock", "--project", str(root), "--upgrade-package", "pysx"], check=True)
    command = [uv, "sync", "--project", str(root), "--python", sys.executable]

    if args.editor:
        command.extend(("--extra", "editor"))
    subprocess.run(command, check=True)
    sys.stdout.write("Ready: uv run --project tutorial pysx-tutorial\n")


if __name__ == "__main__":
    main()
