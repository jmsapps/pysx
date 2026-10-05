"""Validated two-way control bindings and form transaction metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from .reactive import Signal

if TYPE_CHECKING:
    from .wire import Op


@dataclass
class Binding:
    signal: Signal[object]
    element: str
    mode: str
    radio_value: str | None = None

    @property
    def name(self) -> str:
        return {"checked": "checked", "radio": "checked", "selected": "selectedValues"}.get(
            self.mode, "value"
        )

    def value(self) -> str | bool | list[str]:
        value = self.signal()

        if self.mode == "radio":
            return value == self.radio_value

        return cast("str | bool | list[str]", value)

    def validate(self, value: object) -> None:
        valid = (
            isinstance(value, bool)
            if self.mode == "checked"
            else isinstance(value, list)
            and all(isinstance(item, str) for item in cast("list[object]", value))
            if self.mode == "selected"
            else isinstance(value, str)
        )

        if not valid:
            raise TypeError(f"{self.mode} binding received an incompatible payload")

    def set(self, value: object) -> None:
        self.validate(value)
        self.signal.set(value)

    def op(self) -> Op:
        value = self.value()

        if self.mode == "selected":
            return {
                "op": "prop",
                "id": self.element,
                "name": "selectedValues",
                "v": cast("list[str]", value),
            }

        return {
            "op": "attr",
            "id": self.element,
            "name": self.name,
            "v": ("" if value else None) if isinstance(value, bool) else cast("str", value),
        }


def make_binding(
    name: str,
    tag: str,
    control_type: str,
    multiple: bool,
    value: object,
    element: str,
    radio_value: str | None,
) -> Binding:
    if not isinstance(value, Signal):
        raise TypeError(f"{name} requires a writable signal")
    bound = cast("Signal[object]", value)

    if bound.computation is not None:
        raise TypeError(f"{name} requires a writable signal")

    if tag not in {"input", "select", "textarea"}:
        raise TypeError(f"{name} requires a form control")
    mode = {"bindChecked": "checked", "bindSelected": "selected"}.get(name, "value")

    if mode == "checked" and (tag != "input" or control_type not in {"checkbox", "radio"}):
        raise TypeError("bindChecked requires a checkbox or radio")

    if mode == "selected" and (tag != "select" or not multiple):
        raise TypeError("bindSelected requires a multiple select")

    if mode == "value" and (multiple or control_type in {"checkbox", "file"}):
        raise TypeError("bindValue requires a text control, single select or radio")

    if mode == "value" and control_type == "radio":
        mode = "radio"

        if radio_value is None:
            raise TypeError("radio bindValue requires a value attribute")
    binding = Binding(bound, element, mode, radio_value)
    binding.validate(bound())

    return binding


def form_edits(value: object) -> list[tuple[str, object, int | None]]:
    if value is None:
        return []

    if not isinstance(value, list):
        raise TypeError("form edits must be an array")
    edits: list[tuple[str, object, int | None]] = []

    for entry in cast("list[object]", value):
        if not isinstance(entry, dict):
            raise TypeError("form edits must contain objects")
        edit = cast("dict[str, object]", entry)
        handler = edit.get("h")
        revision = edit.get("rev")

        if not isinstance(handler, str):
            raise TypeError("form edit handler must be a string")

        if revision is not None and (type(revision) is not int or revision < 0):
            raise TypeError("edit revisions must be nonnegative integers")
        edits.append((handler, edit.get("v"), revision))

    return edits
