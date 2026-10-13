"""Safe event outcome contracts."""

import json
from typing import TYPE_CHECKING

from pysx.errors import ErrorInfo

if TYPE_CHECKING:
    from pysx.wire import ResultMessage


def test_boundary_error_safe_metadata_and_distinct_outcomes() -> None:
    first = ErrorInfo("owner", "handler")
    second = ErrorInfo(None, "render")
    assert first.id != second.id
    assert second.wire()["owner"] is None
    result: ResultMessage = {
        "t": "result",
        "h": "h0",
        "status": "error",
        "ops": [],
        "error": first.wire(),
    }
    assert json.loads(json.dumps(result))["error"]["phase"] == "handler"
    assert set(first.wire()) == {"id", "owner", "phase", "message"}
    assert "Traceback" not in json.dumps(result)
