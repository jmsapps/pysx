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
from string.templatelib import Template, convert
from typing import TYPE_CHECKING, cast

from .bindings import Binding, Deferred, Environment, resolve
from .reactive import Signal, derived

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Literal


def template_values(template: Template) -> tuple[object, ...]:
    """Preserve structural values; explicitly formatted Signals remain live."""
    values: list[object] = []

    for interpolation in template.interpolations:
        value = interpolation.value
        conversion = interpolation.conversion
        spec = interpolation.format_spec

        if conversion is None and not spec:
            values.append(value)

            continue

        def formatted(
            value: object = value,
            conversion: Literal["s", "r", "a"] | None = conversion,
            spec: str = spec,
        ) -> str:
            current = cast("Signal[object]", value)() if isinstance(value, Signal) else value
            converted = convert(current, conversion) if conversion is not None else current

            return format(converted, spec)

        if isinstance(value, (Deferred, Binding)):

            def deferred_format(
                environment: Environment,
                original: object = value,
                formatter: Callable[[object], str] = formatted,
            ) -> str:

                return formatter(resolve(original, environment))

            values.append(Deferred(deferred_format))
        else:
            values.append(derived(formatted) if isinstance(value, Signal) else formatted())

    return tuple(values)


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

            for a, b in zip(margin, indent, strict=False):
                if a != b:

                    break
                cut += 1
            margin = margin[:cut]

        if not margin:

            break

    return margin or ""


def _line_starts(strings: Fragments) -> list[str]:
    out: list[str] = []

    for index, frag in enumerate(strings):
        starts = frag.split("\n")[1:]

        if starts and index < len(strings) - 1:
            # A following hole makes even a zero-indent line nonblank. This
            # marker is used only to measure whitespace, never passed to parsing.
            starts[-1] += "\x00"
        out.extend(starts)

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
