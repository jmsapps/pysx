"""Portable sourceless applications from the same compiler used by source imports."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import marshal
import shutil
import sys
import tokenize
from pathlib import Path

from .compiler import VERSION, analyze, import_index

_EXCLUDED = frozenset({"node_modules", "vendor", "site-packages", "__pycache__", "build", "dist"})


def build_tree(source: Path, output: Path) -> tuple[Path, ...]:
    """Compile a package/root into a fresh output directory, including its resources."""
    source, output = source.resolve(), output.resolve()

    if not source.is_dir() or output == source or source.is_relative_to(output):

        raise ValueError("source must be a directory separate from output")

    if output.exists() and any(output.iterdir()):

        raise ValueError("output directory must be empty")
    paths = tuple(
        path
        for path in source.rglob("*")
        if path.is_file()
        and not path.is_relative_to(output)
        and not any(
            part.startswith(".") or part in _EXCLUDED for part in path.relative_to(source).parts
        )
    )
    compiled: list[Path] = []
    manifest: dict[str, str] = {}
    root_package = source.name if (source / "__init__.py").is_file() else ""

    for path in paths:
        relative = path.relative_to(source)
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)

        if path.suffix != ".py":
            if path.suffix != ".pyc":
                shutil.copy2(path, destination)

            continue
        with tokenize.open(path) as stream:
            text = stream.read()
        filename = (Path(root_package) / relative).as_posix()
        parts = (*((root_package,) if root_package else ()), *relative.with_suffix("").parts)
        package = ".".join(parts[:-1])
        compilation = analyze(
            text,
            str(path),
            workspace_roots=(source.parent if root_package else source,),
            package=package,
        )
        tree = compilation.runtime_ast()
        prefix = import_index(tree)

        if not compilation.calls:
            tree.body.insert(
                prefix, ast.Import(names=[ast.alias("pysx._compiler_runtime", compilation.helper)])
            )
        tree.body.insert(
            prefix + 1,
            ast.Expr(
                ast.Call(
                    ast.Attribute(
                        ast.Name(compilation.helper, ast.Load()), "register_source", ast.Load()
                    ),
                    [ast.Constant(filename), ast.Constant(text)],
                    [],
                )
            ),
        )
        ast.fix_missing_locations(tree)
        code = compile(tree, filename, "exec")
        destination = destination.with_suffix(".pyc")
        destination.write_bytes(importlib.util.MAGIC_NUMBER + bytes(12) + marshal.dumps(code))
        compiled.append(destination)
        manifest[filename] = hashlib.sha256(text.encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    (output / "pysx-build.json").write_text(
        json.dumps(
            {
                "compiler": VERSION,
                "python": sys.implementation.cache_tag,
                "magic": importlib.util.MAGIC_NUMBER.hex(),
                "sources": manifest,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return tuple(compiled)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    paths = build_tree(args.source, args.output)
    print(f"compiled {len(paths)} modules into {args.output}")


if __name__ == "__main__":
    main()
