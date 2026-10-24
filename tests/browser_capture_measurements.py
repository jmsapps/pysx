"""Small repeatable local observations for component capture, on available hardware."""

import gc
import json
import platform
import statistics
import sys
import time
from pathlib import Path

import browser_capture_fixture as fixture

from pysx.components.checkpoints import sites
from pysx.runtime.session import Session


def summarize(values: list[float]) -> dict[str, int | float]:
    ordered = sorted(values)

    return {
        "samples": len(values),
        "median_ms": statistics.median(values) * 1000,
        "p95_ms": ordered[int((len(values) - 1) * 0.95)] * 1000,
    }


def measure() -> dict[str, object]:
    construction: list[float] = []

    for index in range(110):
        started = time.perf_counter()
        session = Session(fixture.native_app)
        result = session.rendered.scopes.checkpoints.capture()
        elapsed = time.perf_counter() - started
        assert result.plaintext is not None
        assert result.resets == 0

        if index >= 10:
            construction.append(elapsed)
        session.dispose()
    gc.collect()
    session = Session(fixture.native_app)
    registry = session.rendered.scopes.checkpoints
    changed: list[float] = []
    unchanged: list[float] = []

    for index in range(1000):
        started = time.perf_counter()
        with session.context():
            fixture.held["count"].set(index + 1)
        result = registry.capture()
        changed.append(time.perf_counter() - started)
        assert result.plaintext is not None
        started = time.perf_counter()
        with session.context():
            pass
        assert registry.capture() is result
        unchanged.append(time.perf_counter() - started)
    snapshot = registry.capture()
    assert snapshot.plaintext is not None
    report: dict[str, object] = {
        "environment": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "qualification": "local observations; reference scale checks are post-roadmap",
        "fixture": "native_app: two local slots",
        "construction_and_capture": summarize(construction),
        "changed_turn_and_capture": summarize(changed),
        "unchanged_turn_and_cached_capture": summarize(unchanged),
        "snapshot_bytes": len(snapshot.plaintext),
        "slots": len(registry.slots),
        "metadata_budget_bytes": registry.metadata_bytes,
        "code_cache": sites.cache_info()._asdict(),
    }
    session.dispose()
    assert not registry.slots
    assert registry.cached is None

    return report


if __name__ == "__main__":
    observations = measure()
    output = Path(".artifacts/browser-recovery/capture-local.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(observations, indent=2) + "\n")
    sys.stdout.write(json.dumps(observations) + "\n")
