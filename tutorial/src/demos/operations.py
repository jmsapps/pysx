"""Actual authorization, static analysis, host validation and task workflow."""

import json
from dataclasses import replace
from typing import TYPE_CHECKING

from pysx import (
    AccessDenied,
    Fragment,
    HostConfig,
    SecurityPolicy,
    current_request,
    derived,
    each,
    project,
    pysx,
    require_access,
    signal,
)
from pysx.tooling.check import apply_fix, diagnostics_for_source
from src.components import Actions, Button, Field, Panel, Readout
from src.components.code import CodeBlock
from src.model import LabState

if TYPE_CHECKING:
    from pysx import Signal
    from src.model import Task

FROZEN_SOURCE = '''from pysx import pysx, signal

count = signal(0)
view = pysx(t"""
    p: {count()}
""")
'''
ATTRIBUTE_SOURCE = '''from pysx import pysx

view = pysx(t"""
    div(hreff="bad")
""")
'''
CORRECT_SOURCE = '''from pysx import pysx

view = pysx(t"""
    div(title="valid")
""")
'''


def authentication() -> Fragment:
    user = current_request().principal or "learner"
    count = signal(0)
    result = signal("No write attempted")

    def write(_: object) -> None:
        try:
            require_access("write", "course-demo")
        except AccessDenied:
            result.set("Denied by current host policy")

            return
        count.update(lambda value: value + 1)
        result.set("Allowed by current host policy")

    return pysx(t"""
        p: "Local demonstration identity: "
        Readout(id="auth-principal"): {user}
        Actions:
            a(id="auth-reader" href="/demo-login/reader"): "Sign in as reader"
            a(id="auth-writer" href="/demo-login/writer"): "Sign in as writer"
            a(id="auth-logout" href="/demo-logout"): "Sign out"
        Button(id="auth-write" onClick={write}): "Attempt protected write"
        Readout(id="auth-count"): {count}
        Readout(id="auth-result" role="status"): {result}
        p: "These local roles are not a production credential provider."
    """)


def diagnostics() -> Fragment:
    source = signal(FROZEN_SOURCE)

    def report() -> str:
        findings = diagnostics_for_source(source())

        return "\n".join(item["message"] for item in findings) or "No diagnostics ([])"

    result = derived(report)

    def fix(_: object) -> None:
        for finding in diagnostics_for_source(source()):
            fixes = finding.get("fixes", [])

            if fixes:
                source.set(apply_fix(source(), fixes[0]))

                return
        source.set(CORRECT_SOURCE)

    def warning(_: object) -> None:
        source.set(ATTRIBUTE_SOURCE)

    return pysx(t"""
        CodeBlock(identifier="diagnostic-source" source={source}):
        Actions:
            Button(id="diagnostic-fix" onClick={fix}): "Apply correction"
            Button(id="diagnostic-warning" onClick={warning}): "Show attribute warning"
        Readout(id="diagnostic-result" role="status"): {result}
    """)


def hosting() -> Fragment:
    result = signal("Local host: render capacity 25")

    def invalid(_: object) -> None:
        try:
            HostConfig(production=True)
        except ValueError as error:
            result.set(f"Rejected: {error}")

    def complete(_: object) -> None:
        config = HostConfig(
            production=True,
            secure_cookies=True,
            security=SecurityPolicy(
                origins=("https://course.example",),
                require_principal=True,
                authorize=lambda request, action, target: (
                    request.principal == "writer" and (action != "write" or target == "course-demo")
                ),
            ),
        )
        result.set(f"Accepted complete production configuration: {config.production}")

    return pysx(t"""
        Actions:
            Button(id="host-invalid" onClick={invalid}): "Validate incomplete production profile"
            Button(id="host-complete" onClick={complete}): "Validate complete profile"
        Readout(id="host-result" role="status"): {result}
        p: "This validates configuration only; no remote service is contacted."
    """)


def codec() -> Fragment:
    source = signal('{"draft": "Saved draft", "count": 1}')
    result = signal("Choose Validate to decode the data")

    def validate(_: object) -> None:
        from src.durable import decode

        try:
            if len(source()) > 2048:
                raise ValueError("sample exceeds its input budget")
            value = decode(json.loads(source()))
            result.set(f"Accepted draft: {value.draft}; count: {value.count}")
        except ValueError, TypeError:
            result.set("Rejected: expected a bounded draft and nonnegative integer count")

    def invalid(_: object) -> None:
        source.set('{"draft": 42, "count": "invalid"}')
        validate(None)

    return pysx(t"""
        label(for="codec-source"): "Bounded JSON data, never Python source"
        textarea(id="codec-source" bindValue={source} maxLength={2048} rows={4})
        Actions:
            Button(id="codec-validate" onClick={validate}): "Validate codec sample"
            Button(id="codec-invalid" onClick={invalid}): "Try invalid data"
        Readout(id="codec-result" role="status"): {result}
    """)


def capstone(state: Signal[LabState] | None = None) -> Fragment:
    from src.model import Task

    root = signal(LabState()) if state is None else state
    tasks = project(
        root, lambda value: list(value.tasks), lambda value, rows: replace(value, tasks=tuple(rows))
    )
    draft = signal("")
    only_active = signal(False)
    next_id = max((task.id for task in tasks()), default=0) + 1
    visible = derived(lambda: [row for row in tasks() if not only_active() or not row.done])

    def add(_: object) -> None:
        nonlocal next_id
        text = draft().strip()[:120]

        if text and len(tasks()) < 20:
            tasks.update(lambda rows: [*rows, Task(next_id, text)])
            next_id += 1
            draft.set("")

    def reverse(_: object) -> None:
        tasks.update(lambda rows: list(reversed(rows)))

    def reset(_: object) -> None:
        nonlocal next_id
        root.set(LabState())
        draft.set("")
        only_active.set(False)
        next_id = 3

    def row(item: Task) -> Fragment:
        note = project(
            root,
            lambda value: next((task.note for task in value.tasks if task.id == item.id), ""),
            lambda value, text: replace(
                value,
                tasks=tuple(
                    replace(task, note=text[:512]) if task.id == item.id else task
                    for task in value.tasks
                ),
            ),
        )

        def toggle(_: object) -> None:
            tasks.update(
                lambda values: [
                    replace(task, done=not task.done) if task.id == item.id else task
                    for task in values
                ]
            )

        def remove(_: object) -> None:
            tasks.update(lambda values: [task for task in values if task.id != item.id])

        return pysx(t"""
            li(data-task={item.id} data-complete={str(item.done).lower()})
                h3: {item.title}
                label(for={f"task-note-{item.id}"}): "Private row note"
                Field(id={f"task-note-{item.id}"} bindValue={note})
                Actions:
                    Button(data-task-toggle={item.id} onClick={toggle}): "Toggle complete"
                    Button(data-task-remove={item.id} onClick={remove}): "Remove task"
        """)

    return pysx(t"""
        Panel:
            form(id="task-form" onSubmit={add})
                label(for="new-task"): "New task"
                Field(id="new-task" bindValue={draft} required={True} maxLength={120})
                Button(id="task-add" type="submit"): "Add task"
            Actions:
                Button(id="task-reverse" onClick={reverse}): "Reverse tasks"
                Button(id="task-reset" onClick={reset}): "Reset workspace"
                label(for="task-active" class="check-field")
                    input(id="task-active" type="checkbox" bindChecked={only_active})
                    "Show active tasks only"
            ul(id="tasks"): {each(visible, row, key=lambda item: item.id)}
            p: "Task count: " {derived(lambda: len(tasks()))}
    """)
