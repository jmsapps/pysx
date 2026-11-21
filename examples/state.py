"""Where PYSX keeps an application's managed state, and how to choose it.

By default every framework-managed file for this application lives under the owning
project's `.pysx/` directory, which ignores itself. A deployment that replaces its
checkout points `state_directory` at a persistent volume instead, so recovery keys and
framework-managed stores survive a release.
"""

from __future__ import annotations

import os
from pathlib import Path

from pysx import Fragment, HostConfig, persistent_path, pysx, temporary_path
from pysx.hosting.hosting import Application

from .components import (
    Description,
    Eyebrow,
    Metric,
    Page,
    Title,
    Value,
    example_app,
    example_title,
)

NAMESPACE = "pysx-example-state"
# A durable root outside a replaceable checkout; unset, this falls back to `.pysx/`.
STATE_ROOT = (
    Path(os.environ["PYSX_EXAMPLE_STATE"]) if os.environ.get("PYSX_EXAMPLE_STATE") else None
)


def app() -> Fragment:
    example_title("Managed state")
    persistent = persistent_path(NAMESPACE, directory=STATE_ROOT)
    disposable = temporary_path(NAMESPACE, "example", directory=STATE_ROOT)

    return pysx(t"""
        Page:
            Eyebrow: "Application state"
            Title: "One place for managed state"
            Description:
                "Durable state and disposable working files are separated, so cleanup
                 can remove the second without ever reaching the first."
            Metric:
                Value: {str(persistent)}
            Metric:
                Value: {str(disposable)}
    """)


example = example_app(
    Application(
        app,
        config=HostConfig(state_directory=STATE_ROOT, state_namespace=NAMESPACE),
    )
)
