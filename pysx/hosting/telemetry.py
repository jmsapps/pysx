"""Safe bounded telemetry: no paths, identities, credentials or payload values."""

import logging
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestEvent:
    request_id: str
    operation: str
    status: int


def record(event: RequestEvent) -> None:
    logging.getLogger("pysx.access").info(
        "request completed",
        extra={
            "request_id": event.request_id,
            "operation": event.operation,
            "status": event.status,
        },
    )
