"""Typed event snapshots and synchronous, declarative browser policies."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal, cast

from .forms import PayloadError

if TYPE_CHECKING:
    from collections.abc import Callable

FIELD_LIMIT = 4096
VALUE_LIMIT = 1 << 20


@dataclass(frozen=True)
class BrowserEvent:
    type: str
    handler: str
    target: str = ""
    value: str = ""
    checked: bool = False
    key: str = ""
    code: str = ""
    alt: bool = False
    ctrl: bool = False
    meta: bool = False
    shift: bool = False
    repeat: bool = False
    composing: bool = False
    button: int = 0
    buttons: int = 0
    x: float = 0
    y: float = 0
    pointer_id: int = 0
    pointer_type: str = ""
    related_target: str = ""
    submitter: str = ""


def decode_event(value: object, handler: str, event_type: str) -> BrowserEvent:
    if not isinstance(value, dict):
        raise PayloadError("event snapshot must be an object")
    fields = cast("dict[str, object]", value)
    defaults = asdict(BrowserEvent(event_type, handler))

    if fields.keys() - defaults.keys() or fields.get("type") != event_type:
        raise PayloadError("unexpected event fields or type")

    if fields.get("handler") != handler:
        raise PayloadError("event handler mismatch")

    for name, item in fields.items():
        default = defaults[name]

        if name in {"x", "y"}:
            valid = type(item) in {int, float} and abs(cast("float", item)) <= 1e9
            valid = valid and math.isfinite(cast("float", item))
        else:
            valid = type(item) is type(default)

        limit = VALUE_LIMIT if name == "value" else FIELD_LIMIT

        if not valid or (isinstance(item, str) and len(item) > limit):
            raise PayloadError(f"invalid event field {name}")
        defaults[name] = item

    return BrowserEvent(**defaults)


@dataclass(frozen=True)
class EventHandler:
    callback: Callable[[BrowserEvent], object]
    prevent_default: bool = False
    stop_propagation: bool = False
    keys: tuple[str, ...] = ()
    phase: Literal["capture", "bubble"] = "bubble"
    eligible_link: bool = False
    event_type: str | None = None

    def __post_init__(self) -> None:
        if self.phase not in {"capture", "bubble"}:
            raise ValueError("event phase must be capture or bubble")

        if len(self.keys) > 32 or any(len(key) > 128 for key in self.keys):
            raise ValueError("key filters are bounded")

        if self.event_type is not None and not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,63}", self.event_type
        ):
            raise ValueError("invalid custom event type")

    def policy(self, event_type: str) -> dict[str, object]:
        return {
            "type": self.event_type or event_type,
            "prevent": self.prevent_default,
            "stop": self.stop_propagation,
            "keys": self.keys,
            "phase": self.phase,
            "link": self.eligible_link,
        }


def on_event(
    callback: Callable[[BrowserEvent], object],
    *,
    prevent_default: bool = False,
    stop_propagation: bool = False,
    keys: tuple[str, ...] = (),
    phase: Literal["capture", "bubble"] = "bubble",
    eligible_link: bool = False,
    event_type: str | None = None,
) -> EventHandler:
    """Receive an event snapshot; policies execute before any server round trip."""
    return EventHandler(
        callback, prevent_default, stop_propagation, keys, phase, eligible_link, event_type
    )
