"""Real declared-state lab served by the course's single application host."""

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

from pysx import (
    AccessDenied,
    DurableApplication,
    Fragment,
    HostConfig,
    Signal,
    SQLiteOwnershipStore,
    StateCodec,
    derived,
    project,
    push,
    pysx,
    require_access,
    resource,
    signal,
)

from .components import Actions, Button, Field, Panel, Readout
from .model import LabState, Task
from .themes import course_themes

if TYPE_CHECKING:
    from pathlib import Path

    from pysx.runtime.wire import JSONValue


def encode(state: LabState) -> JSONValue:
    return {
        "draft": state.draft,
        "count": state.count,
        "tasks": [
            {"id": task.id, "title": task.title, "done": task.done, "note": task.note}
            for task in state.tasks
        ],
    }


def decode(value: JSONValue) -> LabState:
    if not isinstance(value, dict):
        raise ValueError("expected declared lab state")
    draft, count = value.get("draft"), value.get("count")

    if not isinstance(draft, str) or len(draft) > 512 or type(count) is not int or count < 0:
        raise ValueError("invalid declared lab state")

    rows = value.get("tasks", [])

    if not isinstance(rows, list) or len(rows) > 20:
        raise ValueError("invalid task collection")
    tasks: list[Task] = []

    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid task")
        identifier, title, done, note = (
            row.get("id"),
            row.get("title"),
            row.get("done"),
            row.get("note"),
        )

        if (
            type(identifier) is not int
            or identifier < 1
            or not isinstance(title, str)
            or len(title) > 120
            or type(done) is not bool
            or not isinstance(note, str)
            or len(note) > 512
        ):
            raise ValueError("invalid task")
        tasks.append(Task(identifier, title, done, note))

    if len({task.id for task in tasks}) != len(tasks):
        raise ValueError("duplicate task identifiers")

    return LabState(draft, count, tuple(tasks))


def view(state: Signal[LabState]) -> Fragment:
    from .demos.operations import capstone

    draft = project(
        state, lambda value: value.draft, lambda value, text: replace(value, draft=text[:512])
    )
    count = derived(lambda: state().count)
    setup = uuid4().hex[:10]
    failing = signal(False)
    status = signal("No protected commit attempted")

    async def load() -> str:
        should_fail = await push(failing)
        await asyncio.sleep(0.25)

        if should_fail:
            raise ValueError("intentional capstone validation failure")

        return "Resource rebuilt"

    value = resource("lab-resource", load)
    message = derived(lambda: value().value or value().status)
    resource_status = derived(lambda: value().status)
    workspace = capstone(state)

    def fail(_: object) -> None:
        failing.set(True)
        value.reload()

    def retry(_: object) -> None:
        failing.set(False)
        value.reload()

    result = project(
        state, lambda current: current.count, lambda current, number: replace(current, count=number)
    )

    def increment(_: object) -> None:
        result.update(lambda number: number + 1)

    def commit(_: object) -> None:
        try:
            require_access("write", "course-demo")
        except AccessDenied:
            status.set("Denied by current host policy")

            return
        result.update(lambda number: number + 1)
        status.set("Allowed by current host policy")

    return pysx(
        t"""
        link(rel="stylesheet" href="/assets/course.css")
        Panel(id="durable-lab")
            h2: "Declared-state workspace"
            label(for="lab-draft"): "Committed UI draft"
            Field(id="lab-draft" bindValue={draft} maxLength={512})
            Readout(id="lab-echo"): {draft}
            Actions:
                Button(id="lab-increment" onClick={increment}): "Increment declared count"
                Button(id="lab-commit" onClick={commit}): "Protected commit"
            Readout(id="lab-count"): {count}
            Readout(id="lab-setup"): {setup}
            Readout(id="lab-resource"): {message}
            Readout(id="lab-resource-status" role="status"): {resource_status}
            Readout(id="lab-commit-status" role="status"): {status}
            Actions:
                Button(id="lab-resource-fail" onClick={fail}): "Fail validation"
                Button(id="lab-resource-retry" onClick={retry}): "Retry validation"
            {workspace}
            p: "This iframe owns a separate real durable session. A new document starts fresh."
    """,
        themes=course_themes(),
    )


def create_lab(
    directory: Path, config: HostConfig, *, schema: int = 1
) -> DurableApplication[LabState]:
    directory.mkdir(parents=True, exist_ok=True)

    return DurableApplication(
        view,
        initial=LabState,
        codec=StateCodec(encode, decode),
        store=SQLiteOwnershipStore(directory / "declared-state.sqlite3"),
        application="pysx-tutorial-lab",
        schema=schema,
        migrations={1: lambda value: encode(decode(value))},
        config=config,
    )
