"""Compile bundled applications before test collection imports them."""

import asyncio
import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pysx.compiler.loader import install_loader

if TYPE_CHECKING:
    from collections.abc import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tutorial"))
install_loader(packages=("examples", "src"))


def _pending_task_stacks() -> str:
    """Name the awaits a hung test is parked on.

    `faulthandler` dumps thread stacks, which for a stalled event loop only ever
    show the selector. A suspended coroutine's frame lives on its Task, so the
    tasks are collected from the object graph rather than from a loop the
    watchdog thread does not belong to.
    """

    import gc
    import traceback

    report: list[str] = []
    pending: list[asyncio.Task[object]] = [
        item for item in gc.get_objects() if isinstance(item, asyncio.Task)
    ]

    for task in pending:
        if task.done():
            continue
        frames = task.get_stack(limit=12)
        rendered = (
            "".join(traceback.format_list(traceback.extract_stack(frames[-1]))) if frames else ""
        )
        report.append(f"--- {task!r}\n{rendered}")

    return "\n".join(report) or "no pending tasks found"


@pytest.fixture(autouse=True)
def _asyncio_hang_watchdog() -> Iterator[None]:
    seconds = float(os.environ.get("PYSX_TASK_WATCHDOG_SECONDS", "90"))

    if seconds <= 0:
        yield

        return

    def report() -> None:
        name = os.environ.get("PYTEST_CURRENT_TEST", "unknown test")
        sys.stderr.write(
            f"\nPYSX TASK WATCHDOG: {name} still running after {seconds}s\n"
            f"{_pending_task_stacks()}\n"
        )
        sys.stderr.flush()

    timer = threading.Timer(seconds, report)
    timer.daemon = True
    timer.start()

    try:
        yield
    finally:
        timer.cancel()
