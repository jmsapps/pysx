"""Compile bounded, indentation-nested component CSS into scoped rules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_DEPTH = 8
MAX_BLOCKS = 128


@dataclass
class Block:
    head: str
    entries: list[str | Block] = field(default_factory=lambda: list[str | Block]())


def parse_css(body: str) -> Block:
    if "{" in body or "}" in body:
        raise ValueError("styled CSS requires declarations and indentation blocks without braces")
    root = Block("")
    stack = [(-1, root)]
    count = 0
    # Comments do not affect indentation or open blocks.
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)

    for line in body.splitlines():
        text = line.strip()

        if not text:
            continue

        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise ValueError("styled CSS indentation uses spaces")
        indent = len(line) - len(line.lstrip())

        while indent <= stack[-1][0]:
            stack.pop()

        if text.endswith(":"):
            count += 1

            if len(stack) > MAX_DEPTH or count > MAX_BLOCKS:
                raise ValueError("styled CSS exceeds 8 levels or 128 blocks")
            head = text[:-1].strip()

            if head.startswith("@"):
                if not head.startswith(("@media ", "@supports ")):
                    raise ValueError("styled CSS supports only @media and @supports blocks")
            elif not head or ";" in head or ":global" in head:
                raise ValueError("invalid scoped selector")
            child = Block(head)
            stack[-1][1].entries.append(child)
            stack.append((indent, child))
        else:
            if ":" not in text:
                raise ValueError("styled CSS expects a declaration or a selector ending in ':'")
            stack[-1][1].entries.append(text.rstrip(";") + ";")

    return root


def _split_selectors(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote = ""
    escaped = False

    for index, char in enumerate(text):
        if escaped:
            escaped = False

            continue

        if char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
        elif char in {"'", '"'}:
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1

            if depth < 0:
                raise ValueError("unbalanced scoped selector")
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1

    if depth or quote or escaped:
        raise ValueError("unbalanced scoped selector")
    parts.append(text[start:].strip())

    if not all(parts):
        raise ValueError("empty scoped selector")

    return parts


def _anchor(selector: str, base: str) -> str:
    # Attribute values and functional pseudo-classes do not introduce sibling
    # combinators. Only replace anchor tokens outside attribute strings.
    depth = 0
    quote = ""
    escaped = False
    output: list[str] = []

    for char in selector:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
        elif char in {"'", '"'}:
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char in "+~" and depth == 0:
            raise ValueError("nested selectors must stay within the component")
        elif char == "&":
            output.append(base)

            continue
        output.append(char)

    return "".join(output)


def _selectors(parent: str, head: str) -> str:
    result: list[str] = []

    for base in _split_selectors(parent):
        for selector in _split_selectors(head):
            selector = selector.strip()

            if "&" in selector:
                if not selector.startswith("&"):
                    raise ValueError("nested selectors must stay within the component")
                selector = _anchor(selector, base.strip())
            else:
                if selector.startswith(("+", "~")):
                    raise ValueError("nested selectors must stay within the component")
                selector = base.strip() + " " + selector
            result.append(selector)

    return ", ".join(result)


def scoped_rules(name: str, body: str) -> str:
    root = parse_css(body)

    def emit(block: Block, selector: str) -> str:
        output: list[str] = []
        declarations: list[str] = []

        def flush() -> None:
            if declarations:
                output.append(selector + " {\n" + "\n".join(declarations) + "\n}")
                declarations.clear()

        for entry in block.entries:
            if isinstance(entry, str):
                declarations.append(entry)
            else:
                flush()

                if entry.head.startswith("@"):
                    output.append(entry.head + " {\n" + emit(entry, selector) + "\n}")
                else:
                    output.append(emit(entry, _selectors(selector, entry.head)))
        flush()

        return "\n".join(output)

    return emit(root, "." + name)
