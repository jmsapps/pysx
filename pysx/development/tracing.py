"""Opt-in bounded event metadata; signal values never enter this pipeline."""

from __future__ import annotations

import secrets
from collections import Counter, deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from inspect import currentframe
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, cast
from urllib.parse import quote

if TYPE_CHECKING:
    from types import FrameType

    from pysx.runtime.wire import JSONValue

type Kind = Literal[
    "signal.write", "dependency", "effect", "template", "render", "patch.queue", "patch.batch"
]

enabled = False
roots: tuple[Path, ...] = ()
active: ContextVar[Trace | None] = ContextVar("pysx_active_trace", default=None)
_PACKAGE = Path(__file__).resolve().parents[1]


def configure(*, enable: bool, source_roots: tuple[Path, ...] = ()) -> None:
    from pysx.reactive import reactive

    global enabled, roots
    enabled = enable
    reactive.trace_enabled = enable
    roots = tuple(root.resolve() for root in source_roots)


class Source(TypedDict):
    file: str
    line: int
    uri: str


class Event(TypedDict):
    sequence: int
    kind: str
    owner: str
    count: int
    value: str
    source: Source | None


def source_link(path: Path, line: int, source_roots: tuple[Path, ...] | None = None) -> Source:
    absolute = path.resolve()
    label = str(absolute)

    for root in roots if source_roots is None else source_roots:
        if absolute.is_relative_to(root):
            label = str(absolute.relative_to(root))

            break

    return {
        "file": label,
        "line": line,
        "uri": f"vscode://file/{quote(absolute.as_posix().lstrip('/'), safe='/:')}:{line}",
    }


def _source() -> Source | None:
    frame: FrameType | None = currentframe()

    try:
        for _ in range(16):
            if frame is None:
                break
            filename = frame.f_code.co_filename

            if not filename.startswith("<"):
                path = Path(filename).resolve()

                if not path.is_relative_to(_PACKAGE) and any(
                    path.is_relative_to(root) for root in roots
                ):
                    return source_link(path, frame.f_lineno)
            frame = frame.f_back
    finally:
        del frame

    return None


@dataclass
class Trace:
    capacity: int = 256
    uid: str = field(default_factory=lambda: secrets.token_urlsafe(12))
    events: deque[Event] = field(init=False)
    counts: Counter[str] = field(default_factory=Counter[str])
    sequence: int = 0
    dropped: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.capacity <= 256:
            raise ValueError("trace capacity must be between 1 and 256")
        self.events = deque(maxlen=self.capacity)

    def record(self, kind: Kind, identity: int = 0, count: int = 0) -> None:
        if not enabled:
            return

        try:
            source = _source()
        except OSError, ValueError, RuntimeError:
            source = None
        self.sequence += 1
        self.counts[kind] += 1

        if len(self.events) == self.capacity:
            self.dropped += 1
        self.events.append(
            {
                "sequence": self.sequence,
                "kind": kind,
                "owner": f"o{identity:x}",
                "count": min(max(count, 0), 1000000),
                "value": "[redacted]",
                "source": source,
            }
        )

    def report(self) -> dict[str, JSONValue]:
        return {
            "id": self.uid,
            "capacity": self.capacity,
            "dropped": self.dropped,
            "counts": dict(self.counts),
            "events": cast("list[JSONValue]", list(self.events)),
        }


def emit(kind: Kind, identity: int = 0, count: int = 0) -> None:
    trace = active.get()

    if trace is not None:
        trace.record(kind, identity, count)
