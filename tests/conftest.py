"""Compile bundled applications before test collection imports them."""

from pysx.loader import install_loader

install_loader(packages=("examples",))
