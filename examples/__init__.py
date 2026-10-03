"""Example registry.

Values are complete server app specs, so a caller can pass one straight to
`python -m pysx.server --app`.
"""

from pathlib import Path

_EXAMPLES_DIR = Path(__file__).resolve().parent

examples: dict[str, str] = {
    path.stem: f"examples.{path.stem}:app"
    for path in sorted(_EXAMPLES_DIR.glob("*.py"))
    if path.name != "__init__.py"
}

__all__ = ["examples"]
