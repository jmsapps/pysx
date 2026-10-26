"""Compile bundled applications before test collection imports them."""

import sys
from pathlib import Path

from pysx.compiler.loader import install_loader

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tutortial"))
install_loader(packages=("examples", "tutorial"))
