"""Browser-local behaviour an application declares instead of writing JavaScript.

Three kinds of value live here, and they are deliberately not Signals: the browser owns
them, the server never observes them, and they stay live while the transport is down.

- Behaviour descriptors (`Reveal`) attach to an element and let the client own
  observation, insertion, removal and cleanup.
- Local actions (`DisconnectFor`) occupy an ordinary handler position but execute in the
  client, which is the only place some of them *can* run.
- Local bindings (`browser.connection()`) project client state into markup.

Everything here serialises to a small closed set of operations the protocol validates.
There is no expression language, no string DSL and no raw-JavaScript escape hatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast, final

if TYPE_CHECKING:
    from collections.abc import Iterable

CONNECTION_STATES = ("connected", "reconnecting", "fresh", "stopped", "disconnected")
ConnectionState = Literal["connected", "reconnecting", "fresh", "stopped", "disconnected"]
MAX_DISCONNECT_SECONDS = 10
MAX_CLASS_LENGTH = 128
MAX_MARGIN_LENGTH = 64
_MARGIN_UNITS = ("px", "%")


class BrowserBehaviorError(ValueError):
    """Raised when a declared behaviour could not be expressed to the client."""


def _class_name(value: str, field_name: str) -> str:
    if not value or len(value) > MAX_CLASS_LENGTH:
        raise BrowserBehaviorError(f"{field_name} must be 1 to {MAX_CLASS_LENGTH} characters")

    if any(character.isspace() for character in value):
        raise BrowserBehaviorError(f"{field_name} must be a single class name")

    return value


def _root_margin(value: str) -> str:
    """Accept the CSS margin shorthand the observer takes, and nothing else."""

    if len(value) > MAX_MARGIN_LENGTH:
        raise BrowserBehaviorError("root_margin is too long")
    parts = value.split()

    if not 1 <= len(parts) <= 4:
        raise BrowserBehaviorError("root_margin takes one to four lengths")

    for part in parts:
        number = part
        unit = next((suffix for suffix in _MARGIN_UNITS if part.endswith(suffix)), None)

        if unit is None:
            raise BrowserBehaviorError(f"root_margin lengths need a px or % unit: {part!r}")
        number = part[: -len(unit)]

        try:
            float(number)
        except ValueError:
            raise BrowserBehaviorError(f"root_margin length is not a number: {part!r}") from None

    return value


@final
@dataclass(frozen=True, slots=True)
class Reveal:
    """Add a class when the element first enters the viewport.

    Reduced motion is part of the behaviour rather than a separate preference API: when
    the user asks for reduced motion the class is applied immediately, including when
    that preference changes later.
    """

    visible_class: str = "is-visible"
    root_margin: str = "0px"
    threshold: float = 0.0
    once: bool = True

    def __post_init__(self) -> None:
        _class_name(self.visible_class, "visible_class")
        _root_margin(self.root_margin)

        if not 0.0 <= self.threshold <= 1.0:
            raise BrowserBehaviorError("threshold must be between 0 and 1")

    def payload(self) -> dict[str, object]:
        return {
            "kind": "reveal",
            "visibleClass": self.visible_class,
            "rootMargin": self.root_margin,
            "threshold": self.threshold,
            "once": self.once,
        }


@final
@dataclass(frozen=True, slots=True)
class DisconnectFor:
    """Drop the transport for a bounded interval, from an ordinary handler position.

    This cannot be a server command: it severs the connection such a command would
    travel over, so the client has to own it.
    """

    seconds: float

    def __post_init__(self) -> None:
        if not 0 < self.seconds <= MAX_DISCONNECT_SECONDS:
            raise BrowserBehaviorError(
                f"seconds must be above 0 and at most {MAX_DISCONNECT_SECONDS}"
            )

    def payload(self) -> dict[str, object]:
        return {"kind": "disconnect", "seconds": self.seconds}


@final
@dataclass(frozen=True, slots=True)
class LocalText:
    """Text chosen in the client from the current connection state."""

    states: tuple[tuple[str, str], ...]
    default: str

    def payload(self) -> dict[str, object]:
        return {
            "kind": "connection-text",
            "states": [list(pair) for pair in self.states],
            "default": self.default,
        }


@final
@dataclass(frozen=True, slots=True)
class LocalFlag:
    """A boolean the client derives from the current connection state."""

    states: tuple[str, ...]
    negate: bool = False

    def payload(self) -> dict[str, object]:
        return {
            "kind": "connection-flag",
            "states": list(self.states),
            "negate": self.negate,
        }


def _states(names: tuple[str, ...]) -> tuple[str, ...]:
    for name in names:
        if name not in CONNECTION_STATES:
            raise BrowserBehaviorError(
                f"unknown connection state {name!r}; expected one of {', '.join(CONNECTION_STATES)}"
            )

    return names


@final
@dataclass(frozen=True, slots=True)
class Connection:
    """Browser-local connection state, projected into ordinary markup.

    Python cannot read the current value: it lives in the browser and remains correct
    while the transport is down, which is exactly when the server cannot be asked.
    """

    def text(self, *, default: str, **states: str) -> LocalText:
        _states(tuple(states))

        return LocalText(states=tuple(states.items()), default=default)

    def is_state(self, *names: str) -> LocalFlag:
        if not names:
            raise BrowserBehaviorError("name at least one connection state")

        return LocalFlag(states=_states(names))

    @property
    def connected(self) -> LocalFlag:
        return LocalFlag(states=("connected",))

    @property
    def not_connected(self) -> LocalFlag:
        return LocalFlag(states=("connected",), negate=True)


@final
@dataclass(frozen=True, slots=True)
class Browser:
    """Entry point for browser-local values."""

    def connection(self) -> Connection:
        return Connection()


browser = Browser()

BehaviorDescriptor = Reveal
LocalAction = DisconnectFor
LocalBinding = LocalText | LocalFlag


def behavior_payload(behaviors: object) -> list[dict[str, object]]:
    """Normalise one descriptor or a collection of them, rejecting duplicates."""

    if isinstance(behaviors, Reveal):
        found: tuple[Reveal, ...] = (behaviors,)
    elif isinstance(behaviors, (list, tuple, set, frozenset)):
        collected: list[Reveal] = []

        for item in cast("Iterable[object]", behaviors):
            if not isinstance(item, Reveal):
                raise BrowserBehaviorError(f"unsupported behavior: {item!r}")
            collected.append(item)
        found = tuple(collected)
    else:
        raise BrowserBehaviorError(f"unsupported behavior: {behaviors!r}")
    kinds = [item.payload()["kind"] for item in found]

    if len(set(kinds)) != len(kinds):
        raise BrowserBehaviorError("a behavior kind may be attached only once per element")

    return [item.payload() for item in found]
