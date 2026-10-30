"""Authorization, tooling, hosting and a coherent final application."""

from textwrap import dedent

from src.content import Lesson

LESSONS = (
    Lesson(
        24,
        "authentication",
        "Authentication and authorization",
        "Build and operate",
        "Apply current host authorization to an operation and keep login generations separate.",
        "Authentication identifies the current principal and login session. Authorization "
        "decides whether that principal may perform a particular operation on a target. "
        "A hidden button is not an authorization check; the handler must still enforce policy.\n\n"
        "This course has explicitly labeled local demonstration roles. The login links "
        "change a demo cookie and create a new authentication generation. They are not "
        "a production credential provider and do not ask you for a real password.",
        "The host resolves identity for HTTP and socket operations. `require_access` "
        "checks the current policy at the write boundary. The reader may edit its own "
        "private UI draft while a protected commit remains denied.\n\n"
        "Identity changes must not import another login's delivered state. Recovery "
        "tests need both a same-generation restoration control and a changed-generation "
        "rejection; a system that never restores anything would otherwise appear secure "
        "by passing only the negative case.",
        dedent('''
            from pysx import (
                AccessDenied,
                Fragment,
                RequestContext,
                SecurityPolicy,
                current_request,
                pysx,
                require_access,
                signal,
            )


            def authorize(request: RequestContext, action: str, target: str) -> bool:
                writer = request.principal == "writer"

                return action != "write" or (writer and target == "course-demo")


            def app() -> Fragment:
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
                    output(id="auth-principal"): {user}
                    div:
                        a(id="auth-reader" href="/demo-login/reader"): "Sign in as reader"
                        a(id="auth-writer" href="/demo-login/writer"): "Sign in as writer"
                        a(id="auth-logout" href="/demo-logout"): "Sign out"
                    button(id="auth-write" onClick={write}): "Attempt protected write"
                    output(id="auth-count"): {count}
                    output(id="auth-result" role="status"): {result}
                    p: "These local roles are not a production credential provider."
                """)


            policy = SecurityPolicy(authorize=authorize)
        ''').strip(),
        "`authorize` permits a protected write only for writer and the course-demo target. The "
        "handler calls require_access before incrementing; denied writes produce the visible "
        "denial. The course host installs this policy and resolves the role cookie from its sign- "
        "in links; those demo links are not a production identity provider.",
        "Sign in as reader and attempt a protected write. Repeat as writer, then sign out "
        "and inspect fresh state.",
        "Reader receives a visible denial; writer succeeds. A new login generation starts "
        "its own authorized state.",
        "Check authorization at the operation, and bind continuity to the current login session.",
        ("session_security",),
    ),
    Lesson(
        25,
        "development-workflow",
        "Development workflow and editor tools",
        "Build and operate",
        "Use actual static diagnostics and a compatible watch workflow without executing samples.",
        "Fast feedback helps you reason about live templates before opening a browser. "
        "The checker and editor understand markup alongside Python, but template "
        "diagnostics remain advisory. A successful editor check is not a runtime proof.\n\n"
        "The source panel below is analyzed as text. Its fix changes an eager `count()` "
        "hole to a live `count` reference. This is safe static analysis, not evaluation of "
        "whatever source a learner supplies.",
        "Watching requires a server you control, so this lesson's exercise uses a "
        "playground rather than the course you are reading. Scaffold the counter from "
        "lesson 1 again, then run it with `pysx --app playgrounds.counter --watch` and edit "
        "`playgrounds/counter.py` while it serves. Compatible "
        "saved edits preserve supported UI state while a broken candidate leaves the "
        "previous worker available. The documented CLI uses the same application entry "
        "as ordinary serving.\n\n"
        "Install the language-server extra and select that environment in the editor. "
        "Keep Python/Pylance ownership of Python expressions and use the framework's "
        "template-aware tooling for markup. The final installed qualification uses the "
        "real editor host, not a mock completion provider.",
        dedent("""
            from pysx import Fragment, derived, pysx, signal
            from pysx.tooling.check import apply_fix, diagnostics_for_source

            FROZEN_SOURCE = '''from pysx import pysx, signal

            count = signal(0)
            view = pysx(t\"\"\"
                p: {count()}
            \"\"\")
            '''

            ATTRIBUTE_SOURCE = '''from pysx import pysx

            view = pysx(t\"\"\"
                div(hreff="bad")
            \"\"\")
            '''

            CORRECT_SOURCE = '''from pysx import pysx

            view = pysx(t\"\"\"
                div(title="valid")
            \"\"\")
            '''


            def app() -> Fragment:
                source = signal(FROZEN_SOURCE)

                def report() -> str:
                    findings = diagnostics_for_source(source())

                    return "\\n".join(item["message"] for item in findings) or "No diagnostics ([])"

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

                return pysx(t\"\"\"
                    pre: {source}
                    div:
                        button(id="diagnostic-fix" onClick={fix}): "Apply correction"
                        button(id="diagnostic-warning" onClick={warning}): "Show attribute warning"
                    output(id="diagnostic-result" role="status"): {result}
                \"\"\")
        """).strip(),
        "The initial source contains the eager count() hole. Apply correction uses the actual "
        "suggested fix; the second sample has the misspelled hreff attribute and the final valid "
        "sample uses title. diagnostics_for_source reads source without executing it. The course "
        "adds syntax highlighting to the same source display.",
        "Inspect the frozen-hole warning, apply its real suggested fix, then show and "
        "correct an attribute warning.",
        "The actual diagnostic report changes with the sample, and a repaired sample has "
        "no finding.",
        "Combine static feedback, a real watch journey and runtime verification.",
        ("development", "diagnostics", "editor_features"),
    ),
    Lesson(
        26,
        "hosting-and-deployment",
        "Hosting and deployment",
        "Build and operate",
        "Validate a host configuration and distinguish local teaching defaults from "
        "production policy.",
        "An application needs operational boundaries as well as attractive markup. "
        "`HostConfig` owns budgets, assets, lifecycle hooks and security policy. Standalone "
        "and optional ASGI adapters serve the same application engine.\n\n"
        "A loopback teaching server is not a production authentication/TLS setup. The "
        "`production` flag validates required security choices instead of silently "
        "accepting an unsafe collection of defaults.",
        "The configuration controls below instantiate real `HostConfig` values. Select "
        "an invalid production profile and inspect its rejection, then choose the complete "
        "profile. No remote host is contacted and no credentials are collected.\n\n"
        "Read the course runbook before exposing the application: use a real identity "
        "provider, explicit HTTPS origins, secure cookies and a deliberate authorization "
        "policy. Health checks and graceful shutdown are part of deployment. Keep the "
        "state/key directory stable across the restarts you expect to recover from.",
        dedent('''
            from pysx import Fragment, HostConfig, RequestContext, SecurityPolicy, pysx, signal


            def app() -> Fragment:
                result = signal("Local host: render capacity 25")

                def invalid(_: object) -> None:
                    try:
                        HostConfig(production=True)
                    except ValueError as error:
                        result.set(f"Rejected: {error}")

                def complete(_: object) -> None:
                    def authorize(
                        request: RequestContext,
                        action: str,
                        target: str,
                    ) -> bool:
                        writer = request.principal == "writer"

                        return writer and (action != "write" or target == "course-demo")

                    config = HostConfig(
                        production=True,
                        secure_cookies=True,
                        security=SecurityPolicy(
                            origins=("https://course.example",),
                            require_principal=True,
                            authorize=authorize,
                        ),
                    )
                    result.set(f"Accepted complete production configuration: {config.production}")

                return pysx(t"""
                    div:
                        button(id="host-invalid" onClick={invalid}):
                            "Validate incomplete production profile"
                        button(id="host-complete" onClick={complete}): "Validate complete profile"
                    output(id="host-result" role="status"): {result}
                    p: "This validates configuration only; no remote service is contacted."
                """)
        ''').strip(),
        "The controls instantiate the same real HostConfig profiles as the live validator: "
        "incomplete production is rejected, while the complete profile declares HTTPS origin, "
        "secure cookies, identity and authorization. Reset demo restores the local-mode readout; "
        "validation does not start or deploy a server.",
        "Try the incomplete production configuration, select the complete profile, then "
        "reset to local mode.",
        "The validator rejects missing security requirements and accepts explicitly "
        "complete configuration.",
        "Treat deployment policy, budgets and lifecycle as application behavior that can "
        "be tested.",
        ("session_security", "durable_state"),
    ),
    Lesson(
        27,
        "capstone-workspace",
        "Build the capstone workspace",
        "Build and operate",
        "Combine the course's state, keyed forms, components, routes and themes in one "
        "useful workflow.",
        "Now bring the pieces together in a project workspace. Individual demonstrations "
        "teach one boundary at a time; a coherent application reveals where those "
        "boundaries interact. A record edit changes derived filters, but must not move "
        "its private draft to a different row.\n\n"
        "The capstone uses the tutorial's own shared controls and theme tokens. A new "
        "task receives a stable identifier. Editing replaces the matching record while "
        "reordering changes only order. Routes and application-level state have deliberate owners.",
        "Create a task, edit its note, mark it complete and filter the list. Reverse "
        "the rows while a draft has focus. These are ordinary user actions, not isolated "
        "API calls; the app should keep the full story consistent.\n\n"
        "Use Reset workspace to return to the starting dataset. A page refresh is a "
        "different operation with session semantics; do not use it as a hidden substitute "
        "for a broken reset. The next lesson extends this same application with failure "
        "and recovery rather than replacing it with a different showcase.",
        dedent('''
            from dataclasses import dataclass, replace
            from pysx import Fragment, Signal, derived, each, project, pysx, signal


            @dataclass(frozen=True)
            class Task:
                id: int
                title: str
                done: bool = False
                note: str = ""


            @dataclass(frozen=True)
            class LabState:
                draft: str = ""
                count: int = 0
                tasks: tuple[Task, ...] = (
                    Task(
                        1,
                        "Read the lesson",
                    ),
                    Task(
                        2,
                        "Build the interface",
                    ),
                )


            def app(state: Signal[LabState] | None = None) -> Fragment:
                root = signal(LabState()) if state is None else state
                tasks = project(
                    root,
                    lambda value: list(
                        value.tasks,
                    ),
                    lambda value, rows: replace(
                        value,
                        tasks=tuple(
                            rows,
                        ),
                    ),
                )
                draft = signal("")
                only_active = signal(False)
                next_id = max((task.id for task in tasks()), default=0) + 1
                visible = derived(
                    lambda: [row for row in tasks() if not only_active() or not row.done],
                )

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
                        lambda value: next(
                            (task.note for task in value.tasks if task.id == item.id),
                            "",
                        ),
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
                            input(id={f"task-note-{item.id}"} bindValue={note})
                            div:
                                button(data-task-toggle={item.id} onClick={toggle}):
                                    "Toggle complete"
                                button(data-task-remove={item.id} onClick={remove}): "Remove task"
                    """)

                return pysx(t"""
                    section:
                        form(id="task-form" onSubmit={add})
                            label(for="new-task"): "New task"
                            input(id="new-task" bindValue={draft} required={True} maxLength={120})
                            button(id="task-add" type="submit"): "Add task"
                        div:
                            button(id="task-reverse" onClick={reverse}): "Reverse tasks"
                            button(id="task-reset" onClick={reset}): "Reset workspace"
                            label(for="task-active" class="check-field")
                                input(id="task-active" type="checkbox" bindChecked={only_active})
                                "Show active tasks only"
                        ul(id="tasks"): {each(visible, row, key=lambda item: item.id)}
                        p: "Task count: " {derived(lambda: len(tasks()))}
                """)
        ''').strip(),
        "The initial records are Read the lesson and Build the interface. Add allocates a stable "
        "id; each row projects its note into the same root. Reverse preserves the record identity "
        "and note, Toggle complete updates the record, and the active filter derives the visible "
        "list. Reset workspace restores the initial dataset.",
        "Add a task, edit a row note, reverse, toggle completion and test the active "
        "filter. Reset and repeat.",
        "The same row retains its note across movement, filters show the correct records "
        "and reset restores the dataset.",
        "A good capstone verifies a complete user journey across several ownership boundaries.",
        ("todos", "keyed_rows", "forms", "navigation", "themes"),
    ),
    Lesson(
        28,
        "capstone-resilience",
        "Make the capstone resilient",
        "Build and operate",
        "Add real async failure/retry, authorization and reconstruction to the completed "
        "workspace.",
        "A useful application must explain what happens when work fails. Extend the "
        "workspace with an owned validation resource, a protected commit and an explicit "
        "declared state root. A retry changes only the work that needs to be retried.\n\n"
        "The resilient capstone uses the same real durable lab as the persistence "
        "lesson, with a complete task journey rather than an invented saved label. "
        "A new setup marker after worker loss proves that code and resources were rebuilt.",
        "Edit a draft, perform an authorized commit, simulate a resource failure and "
        "retry it. Keep the document open through the documented server restart, then "
        "continue using rebuilt handlers. Compare a new tab and an authentication change.\n\n"
        "These are several different guarantees: live reattach, reconstruction of "
        "committed declared state, current authorization and ordinary fresh-session "
        "behavior. None promises exactly-once external transactions. The installed "
        "application checks and your final review judge this entire story together.",
        dedent('''
            import asyncio
            import json
            from dataclasses import dataclass, replace
            from pathlib import Path
            from pysx import (
                AccessDenied,
                DurableApplication,
                Fragment,
                HostConfig,
                SQLiteOwnershipStore,
                Signal,
                StateCodec,
                derived,
                each,
                project,
                push,
                pysx,
                require_access,
                resource,
                signal,
            )
            from pysx.runtime.wire import JSONValue
            from uuid import uuid4


            @dataclass(frozen=True)
            class Task:
                id: int
                title: str
                done: bool = False
                note: str = ""


            @dataclass(frozen=True)
            class LabState:
                draft: str = ""
                count: int = 0
                tasks: tuple[Task, ...] = (
                    Task(
                        1,
                        "Read the lesson",
                    ),
                    Task(
                        2,
                        "Build the interface",
                    ),
                )


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

                if (
                    not isinstance(
                        draft,
                        str,
                    )
                    or len(
                        draft,
                    )
                    > 512
                    or type(
                        count,
                    )
                    is not int
                    or count < 0
                ):
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


            def capstone(state: Signal[LabState] | None = None) -> Fragment:
                root = signal(LabState()) if state is None else state
                tasks = project(
                    root,
                    lambda value: list(
                        value.tasks,
                    ),
                    lambda value, rows: replace(
                        value,
                        tasks=tuple(
                            rows,
                        ),
                    ),
                )
                draft = signal("")
                only_active = signal(False)
                next_id = max((task.id for task in tasks()), default=0) + 1
                visible = derived(
                    lambda: [row for row in tasks() if not only_active() or not row.done],
                )

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
                        lambda value: next(
                            (task.note for task in value.tasks if task.id == item.id),
                            "",
                        ),
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
                            input(id={f"task-note-{item.id}"} bindValue={note})
                            div:
                                button(data-task-toggle={item.id} onClick={toggle}):
                                    "Toggle complete"
                                button(data-task-remove={item.id} onClick={remove}): "Remove task"
                    """)

                return pysx(t"""
                    section:
                        form(id="task-form" onSubmit={add})
                            label(for="new-task"): "New task"
                            input(id="new-task" bindValue={draft} required={True} maxLength={120})
                            button(id="task-add" type="submit"): "Add task"
                        div:
                            button(id="task-reverse" onClick={reverse}): "Reverse tasks"
                            button(id="task-reset" onClick={reset}): "Reset workspace"
                            label(for="task-active" class="check-field")
                                input(id="task-active" type="checkbox" bindChecked={only_active})
                                "Show active tasks only"
                        ul(id="tasks"): {each(visible, row, key=lambda item: item.id)}
                        p: "Task count: " {derived(lambda: len(tasks()))}
                """)


            def view(state: Signal[LabState]) -> Fragment:
                draft = project(
                    state,
                    lambda value: value.draft,
                    lambda value, text: replace(
                        value,
                        draft=text[:512],
                    ),
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
                    state,
                    lambda current: current.count,
                    lambda current, number: replace(
                        current,
                        count=number,
                    ),
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
                    section(id="durable-lab")
                        h2: "Declared-state workspace"
                        label(for="lab-draft"): "Committed UI draft"
                        input(id="lab-draft" bindValue={draft} maxLength={512})
                        output(id="lab-echo"): {draft}
                        div:
                            button(id="lab-increment" onClick={increment}):
                                "Increment declared count"
                            button(id="lab-commit" onClick={commit}): "Protected commit"
                        output(id="lab-count"): {count}
                        output(id="lab-setup"): {setup}
                        output(id="lab-resource"): {message}
                        output(id="lab-resource-status" role="status"): {resource_status}
                        output(id="lab-commit-status" role="status"): {status}
                        div:
                            button(id="lab-resource-fail" onClick={fail}): "Fail validation"
                            button(id="lab-resource-retry" onClick={retry}): "Retry validation"
                        {workspace}
                        p:
                            "This iframe owns a separate real durable session. "
                            "A new document starts fresh."
                """,
                )


            def codec_sample() -> Fragment:
                source = signal('{"draft": "Saved draft", "count": 1}')
                result = signal("Choose Validate to decode the data")

                def validate(_: object) -> None:
                    try:
                        if len(source()) > 2048:
                            raise ValueError("sample exceeds its input budget")
                        value = decode(json.loads(source()))
                        result.set(f"Accepted draft: {value.draft}; count: {value.count}")
                    except ValueError, TypeError:
                        result.set(
                            "Rejected: expected a bounded draft and nonnegative integer count",
                        )

                def invalid(_: object) -> None:
                    source.set('{"draft": 42, "count": "invalid"}')
                    validate(None)

                return pysx(t"""
                    label(for="codec-source"): "Bounded JSON data, never Python source"
                    textarea(id="codec-source" bindValue={source} maxLength={2048} rows={4})
                    div:
                        button(id="codec-validate" onClick={validate}): "Validate codec sample"
                        button(id="codec-invalid" onClick={invalid}): "Try invalid data"
                    output(id="codec-result" role="status"): {result}
                """)


            def create_lab(
                directory: Path, config: HostConfig, *, schema: int = 1
            ) -> DurableApplication[LabState]:
                directory.mkdir(parents=True, exist_ok=True)

                return DurableApplication(
                    view,
                    initial=LabState,
                    codec=StateCodec(encode, decode),
                    store=SQLiteOwnershipStore(directory / "declared-state.sqlite3"),
                    application="declared-state-workspace",
                    schema=schema,
                    config=config,
                )


            codec = StateCodec(encode, decode)
            assert decode({"draft": "Saved draft", "count": 1}).count == 1


            def app() -> Fragment:
                return codec_sample()
        ''').strip(),
        "This combines the same bounded codec, declared workspace, protected commit and owned "
        "validation resource as the embedded lab. Failure and retry replace resource work; "
        "commits check current host authorization. create_lab binds the declared root to a "
        "versioned store, while the setup marker and callbacks are rebuilt. The surrounding "
        "course supplies the authenticated HostConfig and sign-in links; use the durable "
        "playground for your own server restart.",
        "Complete the embedded lab's draft/commit/fail/retry workflow here. Then, for the "
        "restart half, scaffold `pysx-playground durable`, run `pysx --app "
        "playgrounds.durable`, stop it with the tab open and start it again.",
        "Committed declared state is reconstructed, the setup marker changes, retry works "
        "and current policy still applies.",
        "Resilience is an observed workflow across boundaries, not one successful initial render.",
        ("browser_recovery", "durable_state", "resources", "session_security"),
    ),
)
