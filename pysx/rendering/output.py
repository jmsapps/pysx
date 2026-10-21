"""Validation shared by initial markup and reactive output contexts."""

from __future__ import annotations

import html
import re
from typing import LiteralString
from urllib.parse import urlsplit

URL_ATTRIBUTES = frozenset(
    {"href", "src", "action", "formaction", "poster", "cite", "background", "xlink:href"}
)


class TrustedHTML(str):
    """Explicit developer-authored HTML; never construct from network data."""

    def __new__(cls, source: LiteralString) -> TrustedHTML:
        return super().__new__(cls, source)


def safe_url(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("unsafe URL")

    if "\\" in value.partition("#")[0] or urlsplit(value.strip()).scheme.lower() not in (
        "",
        "http",
        "https",
        "mailto",
        "tel",
    ):
        raise ValueError("unsafe URL")

    return value


def safe_css(value: str) -> str:
    if (
        any(character in value for character in "{}<\\\0")
        or "/*" in value
        or re.search(
            r"url\s*\(|expression\s*\(|@import|behavior\s*:|-moz-binding", value, re.IGNORECASE
        )
    ):
        raise ValueError("unsafe dynamic CSS")

    functions = re.findall(r"([A-Za-z_-][A-Za-z0-9_-]*)\s*\(", value)
    permitted = {
        "var",
        "calc",
        "min",
        "max",
        "clamp",
        "round",
        "mod",
        "rem",
        "abs",
        "sign",
        "pow",
        "sqrt",
        "hypot",
        "log",
        "exp",
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
        "atan2",
        "minmax",
        "repeat",
        "fit-content",
        "env",
        "attr",
        "counter",
        "counters",
        "anchor",
        "anchor-size",
        "scroll",
        "view",
        "linear",
        "rgb",
        "rgba",
        "hsl",
        "hsla",
        "hwb",
        "lab",
        "lch",
        "oklab",
        "oklch",
        "color",
        "color-mix",
        "color-contrast",
        "device-cmyk",
        "light-dark",
        "palette-mix",
        "linear-gradient",
        "radial-gradient",
        "conic-gradient",
        "repeating-linear-gradient",
        "repeating-radial-gradient",
        "repeating-conic-gradient",
        "translate",
        "translatex",
        "translatey",
        "translatez",
        "translate3d",
        "scale",
        "scalex",
        "scaley",
        "scalez",
        "scale3d",
        "rotate",
        "rotatex",
        "rotatey",
        "rotatez",
        "rotate3d",
        "skew",
        "skewx",
        "skewy",
        "matrix",
        "matrix3d",
        "perspective",
        "cubic-bezier",
        "circle",
        "ellipse",
        "inset",
        "polygon",
        "path",
        "rect",
        "xywh",
        "ray",
        "shape",
        "steps",
        "blur",
        "brightness",
        "contrast",
        "drop-shadow",
        "grayscale",
        "hue-rotate",
        "invert",
        "opacity",
        "saturate",
        "sepia",
    }

    if any(function.lower() not in permitted for function in functions):
        raise ValueError("unsafe dynamic CSS function")

    return value


def attribute_value(name: str, value: str) -> str:
    if name in URL_ATTRIBUTES:
        safe_url(value)
    elif name in {"srcset", "ping"}:
        for candidate in value.split(",") if name == "srcset" else value.split():
            if candidate.strip():
                safe_url(candidate.strip().split()[0])
    elif name == "style":
        safe_css(value)
    elif name == "srcdoc":
        return value if isinstance(value, TrustedHTML) else html.escape(value)

    return value


def attribute_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]*", name):
        raise ValueError("unsafe attribute name")
