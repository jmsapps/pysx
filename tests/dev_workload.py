"""Reusable fixed editor workspace and focused development entry point."""

from __future__ import annotations

import ast
import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from pysx.runtime.wire import JSONValue

FILES = 200
COMPONENTS = 500
TOTAL_BYTES = 1024 * 1024
LARGEST_BYTES = 100 * 1024
ACTIVE_IMPORTS = 20


def create_workspace(root: Path, *, ordinary: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)

    if any(root.iterdir()):
        raise ValueError("workload generation requires an empty directory")
    quota, extra = divmod(TOTAL_BYTES - LARGEST_BYTES, FILES - 1)

    for index in range(FILES):
        source = "from pysx import Fragment, component, pysx\n"

        if index:
            source += "from leaf_000 import C000_0\n"

        for number in range(3 if index < 100 else 2):
            prefix = "rt" if number % 2 else "t"
            source += f"\n@component\ndef C{index:03}_{number}() -> Fragment:\n"
            source += f"    return pysx({prefix}'''\n        div(style=\"color: red\"):\n"

            if index:
                source += "            C000_0\n"
            source += '            span: "build-0"\n'
            source += '            span: "escaped \\\\t 🐍"\n'
            source += "            span: {(lambda: \"deferred\")}\n    ''')\n"

        if index == FILES - 1:
            dependencies = range(FILES - 1) if ordinary else range(1, ACTIVE_IMPORTS + 1)

            for dependency in dependencies:
                source += f"import leaf_{dependency:03}\n"
            source += (
                "MODULES = (" + ",".join(f"leaf_{value:03}" for value in dependencies) + ",)\n"
            )
            source += """from pathlib import Path
from pysx import DurableApplication, Signal, SQLiteOwnershipStore, StateCodec, native
from pysx.runtime.wire import JSONValue
def decode(value: JSONValue) -> int:
    if type(value) is not int:
        raise ValueError("expected integer")
    return value
def view(state: Signal[int]) -> Fragment:
    def increment(_event: object) -> None:
        state.set(state() + 1)
    return native.Main(C199_0(), MODULES[0].C001_0(),
        native.P(state, id="measure-count"),
        native.Button("Increment", id="measure-increment", onClick=increment))
app = DurableApplication(view, initial=lambda: 7,
    codec=StateCodec(lambda value: value, decode),
    store=SQLiteOwnershipStore(Path(".state.sqlite")), application="measure")
"""

            if ordinary:
                source = source.split("from pathlib import Path\n", 1)[0]
                source += """from pysx import native, signal
def app() -> Fragment:
    count = signal(7)
    def increment(_event: object) -> None:
        count.set(count() + 1)
    children = [getattr(module, name)() for module in MODULES
                for name in vars(module) if name.startswith("C") and name[1:4].isdigit()
                and getattr(module, name).__module__ == module.__name__]
    return native.Main(*children, C199_0(), C199_1(),
        native.P(count, id="measure-count"),
        native.Button("Increment", id="measure-increment", onClick=increment))
"""
            name, size = "user_app.py", LARGEST_BYTES
        else:
            name, size = f"leaf_{index:03}.py", quota + (index < extra)

        if ordinary:
            # Text glyphs avoid color-font rendering in the fully displayed arm.
            source = source.replace("🐍", "λ")
        remaining = size - len(source.encode())
        assert remaining >= 2
        source += "#" + "x" * (remaining - 2) + "\n"
        (root / name).write_text(source)

    assert sum(path.stat().st_size for path in root.glob("*.py")) == TOTAL_BYTES


def describe_workspace(root: Path) -> dict[str, JSONValue]:
    paths = sorted(root.glob("*.py"))
    components = 0
    digest = hashlib.sha256()

    for path in paths:
        body = path.read_bytes()
        digest.update(path.name.encode() + body)
        tree = ast.parse(body.decode())
        components += sum(
            isinstance(node, ast.FunctionDef)
            and any(
                isinstance(decorator, ast.Name) and decorator.id == "component"
                for decorator in node.decorator_list
            )
            for node in ast.walk(tree)
        )

    app_tree = ast.parse((root / "user_app.py").read_text())
    active_imports = sum(
        alias.name.startswith("leaf_")
        for node in ast.walk(app_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    return {
        "files": len(paths),
        "components": components,
        "bytes": sum(path.stat().st_size for path in paths),
        "largest_bytes": max(path.stat().st_size for path in paths),
        "active_imports": active_imports,
        "active_user_modules": min(FILES, active_imports + 2),
        "padding": "comments",
        "unicode": "text glyph" if "λ" in (root / "user_app.py").read_text() else "color emoji",
        "sha256": digest.hexdigest(),
    }
