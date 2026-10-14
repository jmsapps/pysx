"""Safe failure metadata and generation-checked local recovery state."""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from .request import request_context

if TYPE_CHECKING:
    from collections.abc import Callable

    from .wire import ErrorDetail

type ErrorPhase = Literal["render", "handler", "computed", "effect", "cleanup", "mount", "async"]


@dataclass(frozen=True)
class ErrorInfo:
    """Public, safe failure description; exception details stay on the server."""

    owner: str | None
    phase: ErrorPhase
    id: str = field(default_factory=lambda: uuid4().hex)
    message: str = "This part of the application failed. Please try again."

    def wire(self) -> ErrorDetail:
        return {"id": self.id, "owner": self.owner, "phase": self.phase, "message": self.message}


@dataclass
class BoundaryState:
    """Accept failures only from the current live ownership generation."""

    generation: str = field(default_factory=lambda: uuid4().hex)
    error: ErrorInfo | None = None
    alive: bool = True

    def fail(self, generation: str, phase: ErrorPhase) -> ErrorInfo | None:
        if not self.alive or generation != self.generation:
            return None

        if self.error is None:
            self.error = ErrorInfo(self.generation, phase)

        return self.error

    def reset(self, generation: str) -> bool:
        if not self.alive or generation != self.generation:
            return False
        self.error = None
        self.generation = uuid4().hex

        return True

    def close(self) -> None:
        self.alive = False


@dataclass(frozen=True)
class FailureCapture:
    """A callback routes only to the live generation captured at registration."""

    handle: Callable[[Exception, ErrorPhase], bool]


failure_context: ContextVar[FailureCapture | None] = ContextVar("pysx_failure_owner", default=None)


def report_error(info: ErrorInfo, error: Exception, *, development: bool = False) -> None:
    """Log safe correlation data, with source tracebacks only in development."""
    logging.getLogger("pysx.errors").error(
        "application failure %s (%s)",
        info.id,
        info.phase,
        extra={
            "request_id": request.request_id
            if (request := request_context.get()) is not None
            else None,
            "error_id": info.id,
            "phase": info.phase,
        },
        exc_info=(type(error), error, error.__traceback__) if development else None,
    )
