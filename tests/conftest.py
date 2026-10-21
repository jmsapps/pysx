"""Compile bundled applications before test collection imports them."""

from pysx.compiler.loader import install_loader

install_loader(packages=("examples",))
