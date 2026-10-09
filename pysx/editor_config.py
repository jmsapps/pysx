"""Non-destructive project configuration edits for template-aware diagnostics."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from typing import TypedDict, cast

RULES = ("reportUnusedImport", "reportUnusedVariable", "reportUnusedFunction")
MIRRORS = "**/_pysx_revision_*/**"


class ConfigurationEdit(TypedDict):
    filename: str
    source: str
    text: str


def _jsonc(source: str, *, trailing: bool = True) -> str:
    """Blank comments and trailing commas without changing token offsets."""
    pattern = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)
    clean = pattern.sub(
        lambda match: (
            match[0]
            if match[0].startswith('"')
            else "".join("\n" if char == "\n" else " " for char in match[0])
        ),
        source,
    )

    if not trailing:
        return clean

    return re.sub(
        r'"(?:\\.|[^"\\])*"|,(?=\s*[}\]])',
        lambda match: " " if match[0] == "," else match[0],
        clean,
    )


def _paths(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("Pyright ignore must be a list of paths")
    paths = cast("list[object]", value)

    if any(not isinstance(item, str) for item in paths):
        raise ValueError("Pyright ignore must be a list of paths")

    return cast("list[str]", value)


def _ignore(value: object) -> list[str]:
    return list(dict.fromkeys([*_paths(value), MIRRORS]))


def _json_edit(source: str, inherited_ignore: list[str] | None = None) -> str:
    clean = _jsonc(source)
    data = json.loads(clean)

    if not isinstance(data, dict):
        raise ValueError("Pyright configuration must be an object")
    settings = cast("dict[str, object]", data)
    updates: dict[str, object] = dict.fromkeys(RULES, False)
    updates["ignore"] = _ignore(settings.get("ignore", inherited_ignore or []))
    decoder = json.JSONDecoder()
    position = clean.index("{") + 1
    edits: list[tuple[int, int, str]] = []
    found: set[str] = set()

    while True:
        position += len(clean[position:]) - len(clean[position:].lstrip())

        if clean[position] == "}":
            break
        key, key_end = decoder.raw_decode(clean, position)
        position = clean.index(":", key_end) + 1
        position += len(clean[position:]) - len(clean[position:].lstrip())
        _value, end = decoder.raw_decode(clean, position)

        if key in updates:
            edits.append((position, end, json.dumps(updates[key], ensure_ascii=False)))
            found.add(key)
        position = end
        position += len(clean[position:]) - len(clean[position:].lstrip())

        if clean[position] == ",":
            position += 1
    missing = [key for key in updates if key not in found]

    if missing:
        previous = _jsonc(source, trailing=False)[:position].rstrip()
        comma = "" if previous.endswith(("{", ",")) else ","
        additions = ",\n".join(
            f"  {json.dumps(key)}: {json.dumps(updates[key], ensure_ascii=False)}"
            for key in missing
        )
        edits.append((position, position, f"{comma}\n{additions}\n"))

    for start, end, text in sorted(edits, reverse=True):
        source = source[:start] + text + source[end:]

    if json.loads(_jsonc(source)) != settings | updates:
        raise ValueError("cannot safely edit Pyright configuration")

    return source


def _toml_edit(source: str) -> str | None:
    data = tomllib.loads(source)
    settings = data.get("tool", {}).get("pyright")

    if settings is None:
        return None
    headers = list(re.finditer(r"(?m)^[ \t]*\[([^\]\n]+)\][ \t]*(?:#[^\n]*)?$", source))
    sections = [index for index, match in enumerate(headers) if match[1].strip() == "tool.pyright"]

    if len(sections) != 1:
        raise ValueError("save Pyright settings in a single [tool.pyright] table before setup")
    index = sections[0]
    start = headers[index].end()
    end = headers[index + 1].start() if index + 1 < len(headers) else len(source)
    section = source[start:end]
    updates: dict[str, object] = dict.fromkeys(RULES, False)
    updates["ignore"] = _ignore(settings.get("ignore", []))

    for key, value in updates.items():
        literal = "false" if value is False else json.dumps(value, ensure_ascii=False)
        pattern = re.compile(rf'(?m)^([ \t]*["\']?{key}["\']?[ \t]*=[ \t]*)([^\n]*)(\n|$)')
        matches = list(pattern.finditer(section))

        if not matches:
            section = section.rstrip() + f"\n{key} = {literal}\n"
        elif len(matches) == 1:
            match = matches[0]
            value_end = match.end(2)

            if key == "ignore":
                depth = 0
                tokens = re.compile(r'"(?:\\.|[^"\\])*"|\'[^\']*\'|#[^\n]*|[\[\]]')

                for token in tokens.finditer(section, match.start(2)):
                    if token[0] == "[":
                        depth += 1
                    elif token[0] == "]":
                        depth -= 1

                        if depth == 0:
                            value_end = token.end()

                            break
                else:
                    raise ValueError("cannot safely edit Pyright ignore")
            else:
                value_end = match.start(2) + len(match[2].partition("#")[0].rstrip())
            section = section[: match.start(2)] + literal + section[value_end:]
        else:
            raise ValueError(f"cannot safely edit Pyright {key}")
    updated = source[:start] + section.rstrip() + "\n\n" + source[end:]
    expected = data.copy()
    expected["tool"] = dict(data["tool"], pyright=dict(settings, **updates))

    if tomllib.loads(updated) != expected:
        raise ValueError("cannot safely edit Pyright settings without changing other configuration")

    return updated


def _parent_ignore(filename: Path, seen: frozenset[Path] = frozenset()) -> list[str]:
    filename = filename.resolve()

    if filename in seen or len(seen) >= 16 or filename.stat().st_size > 1_048_576:
        raise ValueError("Pyright configuration inheritance is cyclic or exceeds bounds")
    data = json.loads(_jsonc(filename.read_bytes().decode("utf-8-sig")))

    if "ignore" in data:
        paths = _paths(data["ignore"])

        return [str(filename.parent / path) for path in paths]
    parent = data.get("extends")

    if isinstance(parent, str):
        return _parent_ignore(filename.parent / parent, seen | {filename})

    return []


def configuration_edit(root: Path) -> ConfigurationEdit | None:
    root = root.resolve()
    filename = root / "pyrightconfig.json"

    if not filename.is_file():
        filename = root / "pyproject.toml"

    if not filename.is_file():
        return None

    if filename.stat().st_size > 1_048_576:
        raise ValueError("Pyright configuration exceeds 1 MiB")
    source = filename.read_bytes().decode("utf-8-sig")
    working = source.replace("\r\n", "\n")
    inherited: list[str] = []
    text: str | None

    if filename.suffix == ".json":
        parent = json.loads(_jsonc(working)).get("extends")

        if isinstance(parent, str):
            inherited = _parent_ignore(filename.parent / parent)
        text = _json_edit(working, inherited)
    else:
        text = _toml_edit(working)

    if text is not None and "\r\n" in source:
        text = text.replace("\n", "\r\n")

    if text is None or text == source:
        return None

    return ConfigurationEdit(filename=str(filename), source=source, text=text)


def main() -> None:
    sys.stdout.write(json.dumps(configuration_edit(Path(sys.argv[1]))) + "\n")


if __name__ == "__main__":
    main()
