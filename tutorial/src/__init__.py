"""The standalone interactive course application."""


def __getattr__(name: str) -> object:
    """Resolve `app` lazily so importing this package does not load course modules.

    The pysx loader must be installed before any template module is imported, and the
    console script entry point imports this package first.
    """

    if name != "app":
        raise AttributeError(name)

    from .app import app

    return app


def main() -> None:
    """Serve the course, forwarding any pysx server options."""
    import sys

    from pysx.hosting.server import main as serve

    sys.argv = [sys.argv[0], "--app", "src:app", *sys.argv[1:]]
    serve()
