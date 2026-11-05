"""The task workspace with durable records and owned async failure/retry.

pysx-playground capstone
pysx --app playgrounds.capstone
"""

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pysx import (
    DurableApplication,
    Fragment,
    HostConfig,
    Signal,
    SQLiteOwnershipStore,
    StateCodec,
    derived,
    each,
    project,
    push,
    pysx,
    resource,
    signal,
)

if TYPE_CHECKING:
    from pysx.runtime.wire import JSONValue

STORE = Path(__file__).parent / ".state" / "capstone" / "store.sqlite3"


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    done: bool = False
    note: str = ""


@dataclass(frozen=True)
class State:
    tasks: tuple[Task, ...] = (Task(1, "Read the lesson"), Task(2, "Build the interface"))


def encode(state: State) -> JSONValue:
    return {
        "tasks": [
            {"id": task.id, "title": task.title, "done": task.done, "note": task.note}
            for task in state.tasks
        ]
    }


def decode(value: JSONValue) -> State:
    if not isinstance(value, dict):
        raise ValueError("expected workspace state")
    rows = value.get("tasks")

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
            or not title.strip()
            or len(title) > 120
            or type(done) is not bool
            or not isinstance(note, str)
            or len(note) > 512
        ):
            raise ValueError("invalid task")
        tasks.append(Task(identifier, title, done, note))

    if len({task.id for task in tasks}) != len(tasks):
        raise ValueError("duplicate task identifiers")

    return State(tuple(tasks))


def view(state: Signal[State]) -> Fragment:
    setup = uuid4().hex[:12]
    draft = signal("")
    only_active = signal(False)
    failing = signal(False)
    visible = derived(
        lambda: [task for task in state().tasks if not only_active() or not task.done]
    )

    async def validate() -> str:
        snapshot = await push(state)
        should_fail = await push(failing)
        await asyncio.sleep(0.1)

        if should_fail:
            raise ValueError("intentional validation failure")

        return f"Validated {len(snapshot.tasks)} tasks"

    validation = resource("workspace-validation", validate)
    status = derived(lambda: validation().status)
    message = derived(lambda: validation().value or "Use Retry validation after a failure.")

    def fail(_event: object) -> None:
        failing.set(True)
        validation.reload()

    def retry(_event: object) -> None:
        failing.set(False)
        validation.reload()

    def add(_event: object) -> None:
        title = draft().strip()[:120]

        if title and len(state().tasks) < 20:
            identifier = max((task.id for task in state().tasks), default=0) + 1
            state.set(State((*state().tasks, Task(identifier, title))))
            draft.set("")

    def reverse(_event: object) -> None:
        state.set(State(tuple(reversed(state().tasks))))

    def reset(_event: object) -> None:
        state.set(State())
        draft.set("")
        only_active.set(False)
        retry(None)

    def row(task: Task) -> Fragment:
        note = project(
            state,
            lambda value: next((item.note for item in value.tasks if item.id == task.id), ""),
            lambda value, text: State(
                tuple(
                    replace(item, note=text[:512]) if item.id == task.id else item
                    for item in value.tasks
                )
            ),
        )

        def toggle(_event: object) -> None:
            state.set(
                State(
                    tuple(
                        replace(item, done=not item.done) if item.id == task.id else item
                        for item in state().tasks
                    )
                )
            )

        def remove(_event: object) -> None:
            state.set(State(tuple(item for item in state().tasks if item.id != task.id)))

        return pysx(t"""
            li(data-task={task.id})
                h3: {task.title}
                label(htmlFor={f"task-note-{task.id}"}): "Row note"
                input(id={f"task-note-{task.id}"} bindValue={note} maxlength="512")
                div:
                    button(type="button" data-task-toggle={task.id} onClick={toggle}):
                        "Toggle complete"
                    button(type="button" data-task-remove={task.id} onClick={remove}): "Remove task"
        """)

    return pysx(t"""
        main:
            h1: "Resilient task workspace"
            p: "Task records persist. Handlers and validation resources are rebuilt."
            form(onSubmit={add})
                label(htmlFor="new-task"): "New task"
                input(id="new-task" bindValue={draft} required={True} maxlength="120")
                button(id="task-add" type="submit"): "Add task"
            div:
                button(id="task-reverse" type="button" onClick={reverse}): "Reverse tasks"
                button(id="task-reset" type="button" onClick={reset}): "Reset workspace"
            label:
                input(id="task-active" type="checkbox" bindChecked={only_active})
                "Show active tasks only"
            ul(id="tasks"): {each(visible, row, key=lambda task: task.id)}
            h2: "Validation"
            p(id="validation-status" role="status"): {status}
            p(id="validation-message"): {message}
            button(id="validation-fail" type="button" onClick={fail}): "Simulate validation failure"
            button(id="validation-retry" type="button" onClick={retry}): "Retry validation"
            p:
                "Setup marker: "
                span(id="setup"): {setup}
            h2: "Try it"
            ol:
                li: "Add a task, edit its note, reverse tasks, and try the active filter."
                li: "Simulate validation failure, then retry. Your task records stay intact."
                li: "Stop the server with Ctrl+C, leaving this tab open."
                li:
                    "Start the same run command again. Tasks and notes reconstruct; "
                    "the marker changes."
                li: "Add another task and retry validation to exercise the rebuilt handlers."
            p: "Task records are declared state. The new-task draft and filter are temporary UI."
            p: "This local demonstration identity is not production authentication."
    """)


STORE.parent.mkdir(parents=True, exist_ok=True)
app = DurableApplication(
    view,
    initial=State,
    codec=StateCodec[State](encode, decode),
    store=SQLiteOwnershipStore(STORE),
    application="playground-capstone",
    config=HostConfig(principal=lambda _request: "local-playground"),
)
