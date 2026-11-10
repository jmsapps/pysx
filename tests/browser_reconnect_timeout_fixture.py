"""Real browser resume after a deliberately missing first retry hello."""

from pysx import Application, Fragment, HostConfig, TransportLimits
from pysx.compiler.loader import install_loader

install_loader(packages=("examples",))


def view() -> Fragment:
    from examples.reconnect import app as reconnect_app

    return reconnect_app()


app = Application(view, config=HostConfig(transport=TransportLimits(hello_timeout=1)))
