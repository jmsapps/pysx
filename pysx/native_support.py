"""Construction support for typed HTML and deliberate custom element escapes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from string.templatelib import Interpolation, Template
from typing import cast

from .render import Fragment
from .schema import is_event, normalize_attr, tag_info


def element(tag: str, children: tuple[object, ...], attrs: Mapping[str, object]) -> Fragment:
    info = tag_info(tag)

    if not re.fullmatch(r"[a-z][a-z0-9-]*", tag):
        raise ValueError("element names must be lowercase markup names")

    if info is not None and info.void and children:
        raise ValueError(f"{tag} is void and cannot have children")
    expanded = dict(attrs)
    custom = expanded.pop("custom_attrs", None)

    if custom is not None:
        if not isinstance(custom, Mapping):
            raise TypeError("custom_attrs must be a mapping")

        for name, value in cast("Mapping[object, object]", custom).items():
            if not isinstance(name, str):
                raise TypeError("custom attribute names must be strings")
            normalized = normalize_attr(name)

            if normalized.startswith("on") or normalized.startswith("data-pysx-"):
                raise ValueError("event and runtime attributes cannot use custom_attrs")
            expanded[name] = value
    template = Template(f"\n{tag}(")

    for name, value in expanded.items():
        normalized = normalize_attr(name)

        if not re.fullmatch(r"[a-z][a-z0-9-]*", normalized):
            raise ValueError(f"unsafe attribute name {name!r}")

        if is_event(name):
            name = "on" + normalized[2:].capitalize()
        elif name not in {"bind_value", "bind_checked", "bind_selected", "ref"}:
            name = normalized
        elif name != "ref":
            name = {
                "bind_value": "bindValue",
                "bind_checked": "bindChecked",
                "bind_selected": "bindSelected",
            }[name]
        template += Template(f"{name}=", Interpolation(value, name), " ")
    template += Template("): ")

    for child in children:
        template += Template(Interpolation(child, "child"), " ")

    return Fragment(template)


def custom_element(
    tag: str, *children: object, custom_attrs: Mapping[str, object] | None = None
) -> Fragment:
    if "-" not in tag:
        raise ValueError("custom element names must contain a hyphen")

    return element(tag, children, {"custom_attrs": custom_attrs})
