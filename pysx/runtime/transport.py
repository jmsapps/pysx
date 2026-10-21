"""Epoch-local event admission and unconditional owned render snapshots."""

from __future__ import annotations

import math
import secrets
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pysx.rendering.tree import apply, snapshot

from .wire import PROTOCOL_VERSION

if TYPE_CHECKING:
    from .wire import Op


@dataclass(frozen=True)
class TransportLimits:
    graphs: int = 100
    detached: int = 50
    resume_ttl: float = 60
    hello_timeout: float = 5
    send_timeout: float = 2
    close_timeout: float = 2
    max_frame_bytes: int = 65536
    max_batch_bytes: int = 1048576
    outbox_bytes: int = 2097152
    event_queue: int = 64
    socket_queue: int = 16
    write_limit: int = 32768
    ping_interval: float = 20
    ping_timeout: float = 20

    def __post_init__(self) -> None:
        for name in (
            "graphs",
            "detached",
            "max_frame_bytes",
            "max_batch_bytes",
            "outbox_bytes",
            "event_queue",
            "socket_queue",
            "write_limit",
        ):
            value = getattr(self, name)

            if type(value) is not int or value < 1:
                raise ValueError(f"transport {name} must be a positive integer")

        for name in (
            "resume_ttl",
            "hello_timeout",
            "send_timeout",
            "close_timeout",
            "ping_interval",
            "ping_timeout",
        ):
            value = getattr(self, name)

            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"transport {name} must be finite and positive")

        if self.detached > self.graphs or self.max_batch_bytes > self.outbox_bytes:
            raise ValueError("detached/outbound batch limits must fit graph/outbox limits")


class TransportError(OSError):
    """A finite transport deadline or capacity has been exceeded."""


class TransportState:
    def __init__(self, markup: str) -> None:
        self.epoch = secrets.token_urlsafe(24)
        self.revision = 0
        self.sequence = 0
        self.acknowledgements: OrderedDict[int, str] = OrderedDict()
        self.tree = snapshot(markup, unwrap_row=False)

    def apply(self, ops: list[Op]) -> None:
        apply(self.tree, ops)

    def accept(self, message: dict[str, object]) -> str:
        if message["epoch"] != self.epoch:
            return "epoch"
        revision = message["state_rev"]
        assert isinstance(revision, int)

        if revision > self.revision:
            return "revision"
        sequence = message["seq"]
        assert isinstance(sequence, int)

        if sequence <= self.sequence:
            return "duplicate"

        if sequence != self.sequence + 1:
            return "gap"
        self.sequence = sequence

        return "accepted"

    def acknowledge(self, sequence: int, status: str) -> None:
        self.acknowledgements[sequence] = status

        while len(self.acknowledgements) > 64:
            self.acknowledgements.popitem(last=False)

    def envelope(self, message: dict[str, object]) -> dict[str, object]:
        base = self.revision
        self.revision += 1

        return {
            **message,
            "version": PROTOCOL_VERSION,
            "epoch": self.epoch,
            "base_rev": base,
            "state_rev": self.revision,
        }

    def repair(self, css: str) -> dict[str, object]:
        return {
            "t": "repair",
            "html": self.tree.markup(),
            "css": css,
            "expected": self.sequence + 1,
        }
