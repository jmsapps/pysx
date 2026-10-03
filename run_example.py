"""Typer command centre for the bundled examples.

Run from the repository root:  uv run --project . python run_example.py run <name>
"""

from __future__ import annotations

import os
import sys

try:
    import typer
except ModuleNotFoundError:  # pragma: no cover - depends on install mode
    sys.exit(
        "run_example.py needs Typer, which ships in the dev dependency group.\n"
        "run it through uv:  uv run --project . python run_example.py run <name>"
    )

from examples import examples

app = typer.Typer(add_completion=False, help="Run a bundled pysx example.")


@app.callback()
def _main() -> None:
    """Serve the bundled pysx examples.

    A callback is required so Typer keeps `run` as a subcommand; with a single
    command and no callback it flattens into `example <name>`.
    """


def _available() -> str:
    return "\n".join(f"  {name}" for name in examples)


@app.command()
def run(
    name: str = typer.Argument(default="", help="Example to serve."),
    port: int = typer.Option(
        int(os.environ.get("PSX_PORT", "8750")), help="Port to serve on."
    ),
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
) -> None:
    """Serve an example by name."""
    if name not in examples:
        problem = "no example named" if name else "no example given"
        subject = f" {name!r}" if name else ""
        typer.echo(f"{problem}{subject}. available:\n{_available()}", err=True)
        raise typer.Exit(code=2)

    from pysx.server import main as serve

    sys.argv = ["pysx.server", "--app", examples[name], "--port", str(port), "--host", host]
    serve()


if __name__ == "__main__":
    app()
