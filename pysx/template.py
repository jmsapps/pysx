"""Dedent of t-string static fragments.

Fragments after the first begin mid-line, immediately following a `}`, so their
leading text is not a line start. Dedenting fragments individually therefore
reads a zero-width indent and collapses the common margin to nothing. The
margin must be computed across all fragments before anything is substituted,
because an interpolated multi-line value would introduce a zero-indent line and
make the margin empty.
"""

from __future__ import annotations

from functools import lru_cache

Fragments = tuple[str, ...]


def _common_margin(lines: list[str]) -> str:
    """Longest common leading-whitespace prefix, ignoring blank lines."""
    margin: str | None = None
    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            continue
        indent = line[: len(line) - len(stripped)]
        if margin is None:
            margin = indent
        elif indent.startswith(margin):
            continue
        elif margin.startswith(indent):
            margin = indent
        else:
            cut = 0
            for a, b in zip(margin, indent):
                if a != b:
                    break
                cut += 1
            margin = margin[:cut]
        if not margin:
            break
    return margin or ""


def _line_starts(strings: Fragments) -> list[str]:
    out: list[str] = []
    for frag in strings:
        out.extend(frag.split("\n")[1:])
    return out


@lru_cache(maxsize=256)
def dedent_fragments(strings: Fragments) -> Fragments:
    """Strip the common margin from every real line start.

    Keyed on `.strings`, which is identity-stable per call site and hashable by
    value, so each template parses once. Caching on the Template object itself
    would never hit: its __eq__ is identity-based.
    """
    margin = _common_margin(_line_starts(strings))
    if not margin:
        return strings

    width = len(margin)
    last = len(strings) - 1
    out: list[str] = []
    for index, frag in enumerate(strings):
        parts = frag.split("\n")
        end = len(parts) - 1
        rest: list[str] = []
        for position, line in enumerate(parts[1:], start=1):
            # The final line of a fragment that is followed by a hole is a line
            # *continuation*: its text is only the indentation, and the rest of
            # the line lives in the interpolation. Blanking it would destroy
            # that indentation and the node would reparent to column 0.
            continues = index < last and position == end
            if not line.strip() and not continues:
                rest.append("")
            elif line.startswith(margin):
                rest.append(line[width:])
            else:
                rest.append(line)
        out.append("\n".join([parts[0], *rest]))
    return tuple(out)
