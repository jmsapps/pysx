"""Async work, HTTP, isolation and resilient state."""

from textwrap import dedent

from src.content import Lesson

LESSONS = (
    Lesson(
        17,
        "async-resources",
        "Async resources and server updates",
        "Runtime and resilience",
        "Start, replace and cancel owned asynchronous work without stale publication.",
        "An interface must remain usable while work is pending. A resource makes that "
        "lifetime visible through `pending`, `success`, `error` and `cancelled` states. The loader "
        "runs independently, while its eventual publication belongs to the owning session.\n\n"
        "Await slow work outside a synchronous mutation turn. When background work needs "
        "to change signals, await `push` with a synchronous callback. Keeping that boundary "
        "explicit avoids holding a batch or a session mutation across arbitrary I/O.",
        "Reload replaces unfinished work and suppresses results from superseded generations. "
        "Cancellation is therefore more than showing a cancelled label: the old operation "
        "must lose the ability to publish over the current state.\n\n"
        "The local loader below deliberately waits long enough to interact with. Type "
        "a draft while it runs, trigger a replacement, and cancel another request. Your "
        "draft should remain editable and a cancelled result must not appear later.",
        dedent('''
            import asyncio
            from pysx import Fragment, derived, on_mount, on_setup, push, pysx, resource, signal


            def app(*, server_rendering: bool = False) -> Fragment:
                draft = signal("")
                setups = signal(0)
                mounts = signal(0)
                generation = 0
                failing = signal(False)
                on_setup(lambda: setups.update(lambda value: value + 1))
                on_mount(lambda: mounts.update(lambda value: value + 1))

                async def load() -> str:
                    nonlocal generation
                    generation += 1
                    current = generation
                    should_fail = await push(failing)
                    await asyncio.sleep(0.4)

                    if should_fail:
                        raise ValueError("intentional lesson resource failure")

                    return f"Result {current}"

                result = resource("lesson-resource", load)
                status = derived(lambda: result().status)
                message = derived(lambda: result().value or "No completed result")

                def reload(_: object) -> None:
                    failing.set(False)
                    result.reload()

                def cancel(_: object) -> None:
                    result.cancel()

                def fail(_: object) -> None:
                    failing.set(True)
                    result.reload()

                return pysx(t"""
                    label(for="resource-draft"): "Draft stays editable during work"
                    input(id="resource-draft" bindValue={draft})
                    div:
                        button(id="resource-reload" onClick={reload}): "Reload / retry"
                        button(id="resource-cancel" onClick={cancel}): "Cancel"
                        button(id="resource-fail" onClick={fail}): "Fail next request"
                    output(id="resource-status" role="status"): {status}
                    output(id="resource-message"): {message}
                    p: "Server setup: "
                    output(id="resource-setups"): {setups}
                    p: "Browser mount: "
                    output(id="resource-mounts"): {mounts}
                    if {server_rendering}:
                        p: "This content was included in the first HTTP response."
                """)
        ''').strip(),
        "The loader waits 0.4 seconds and returns a numbered result. Reload replaces work, Cancel "
        "revokes the pending request, and Fail next request exercises the error path. The "
        "independent draft remains editable. The status and result readouts derive from the "
        "resource state.",
        "Reload twice before completion. Cancel a third request and wait past its "
        "original deadline.",
        "Only the latest live request publishes. Cancelled work stays cancelled and your "
        "draft remains.",
        "Own asynchronous work, serialize mutations and guard publication by generation.",
        ("resources", "effects"),
    ),
    Lesson(
        18,
        "errors-and-retry",
        "Error boundaries and retry",
        "Runtime and resilience",
        "Recover one failed region without discarding healthy sibling state.",
        "A local failure should have a local recovery boundary when the rest of the "
        "interface can still work. `ErrorBoundary` owns its content and a fallback. A "
        "failure retires the affected region instead of exposing a traceback to the page.\n\n"
        "`ErrorInfo` is deliberately redacted. It provides a safe message and correlation "
        "identity, while the cause stays in server diagnostics. Retry recreates the "
        "failed subtree; it does not undo an already committed business operation.",
        "Keep a healthy sibling outside the boundary. That makes the recovery scope "
        "observable: if its draft or count vanishes, the implementation reset too much. "
        "A component's local state may correctly restart when its own failed lifetime ends.\n\n"
        "The live panel throws a real handler error. Use the sibling while the fallback "
        "is visible, then retry. The fallback's retry callback is owned by the current "
        "boundary generation and should not revive an older discarded instance.",
        dedent('''
            from collections.abc import Callable
            from pysx import ErrorBoundary, ErrorInfo, Fragment, local_state, pysx, signal


            def app() -> Fragment:
                sibling = signal(0)

                def content() -> Fragment:
                    count = local_state("count", 0)

                    def fail(_: object) -> None:
                        raise ValueError("intentional lesson boundary failure")

                    return pysx(t"""
                        section(id="boundary-content")
                            p: "Healthy local panel"
                            output(id="boundary-count"): {count}
                            button(id="boundary-fail" onClick={fail}): "Fail this panel"
                    """)

                def fallback(info: ErrorInfo, retry: Callable[[], None]) -> Fragment:
                    def reset(_: object) -> None:
                        retry()

                    return pysx(t"""
                        section(id="boundary-fallback" role="alert")
                            p: {info.message}
                            button(id="boundary-retry" onClick={reset}): "Retry panel"
                    """)

                Boundary = ErrorBoundary(content, fallback)

                def increment(_: object) -> None:
                    sibling.update(lambda value: value + 1)

                return pysx(t"""
                    Boundary:
                    button(id="sibling-increment" onClick={increment}): "Increment healthy sibling"
                    output(id="sibling-count"): {sibling}
                """)
        ''').strip(),
        "Fail this panel raises inside the boundary. The fallback displays safe ErrorInfo and its "
        "retry button invokes the current retry capability. The sibling counter lives outside the "
        "boundary and remains usable while the failed panel is replaced.",
        "Increment the sibling, fail the panel, increment the sibling again, and retry the panel.",
        "The fallback appears, the sibling keeps its count and the retried panel starts a "
        "fresh lifetime.",
        "A useful boundary contains failure without disguising what a retry can guarantee.",
        ("recovery",),
    ),
    Lesson(
        19,
        "server-rendering",
        "HTTP rendering and browser attachment",
        "Runtime and resilience",
        "Separate server setup, initial HTML and browser mount without duplicating the graph.",
        "Server rendering is not an empty shell waiting for Python to initialize in "
        "the browser. The first HTTP response contains the page's useful content and "
        "styles. Async resources may initially be pending, but the page is already real.\n\n"
        "When the browser attaches, it adopts the staged graph. Running setup again "
        "would duplicate effects, requests and state. Browser mount is a later lifecycle "
        "moment and deserves a separate counter from server setup.",
        "The live component counts setup and mount separately. Its asynchronous message "
        "arrives afterward. Editing the draft before or during that update should not "
        "cause the input to be replaced or the setup count to rise.\n\n"
        "Inspect the page response using the browser's network tools or a command-line "
        "HTTP client, then compare it with the attached document. Initial-response tests "
        "and post-attachment interaction tests establish different parts of the contract.",
        dedent('''
            import asyncio
            from pysx import Fragment, derived, on_mount, on_setup, push, pysx, resource, signal


            def app(*, server_rendering: bool = True) -> Fragment:
                draft = signal("")
                setups = signal(0)
                mounts = signal(0)
                generation = 0
                failing = signal(False)
                on_setup(lambda: setups.update(lambda value: value + 1))
                on_mount(lambda: mounts.update(lambda value: value + 1))

                async def load() -> str:
                    nonlocal generation
                    generation += 1
                    current = generation
                    should_fail = await push(failing)
                    await asyncio.sleep(0.4)

                    if should_fail:
                        raise ValueError("intentional lesson resource failure")

                    return f"Result {current}"

                result = resource("lesson-resource", load)
                status = derived(lambda: result().status)
                message = derived(lambda: result().value or "No completed result")

                def reload(_: object) -> None:
                    failing.set(False)
                    result.reload()

                def cancel(_: object) -> None:
                    result.cancel()

                def fail(_: object) -> None:
                    failing.set(True)
                    result.reload()

                return pysx(t"""
                    label(for="resource-draft"): "Draft stays editable during work"
                    input(id="resource-draft" bindValue={draft})
                    div:
                        button(id="resource-reload" onClick={reload}): "Reload / retry"
                        button(id="resource-cancel" onClick={cancel}): "Cancel"
                        button(id="resource-fail" onClick={fail}): "Fail next request"
                    output(id="resource-status" role="status"): {status}
                    output(id="resource-message"): {message}
                    p: "Server setup: "
                    output(id="resource-setups"): {setups}
                    p: "Browser mount: "
                    output(id="resource-mounts"): {mounts}
                    if {server_rendering}:
                        p: "This content was included in the first HTTP response."
                """)
        ''').strip(),
        "`on_setup` counts the server lifetime, while `on_mount` waits for browser attachment. "
        "The draft and resource share that lifetime. Reload publishes a later result without "
        "rerunning setup or discarding the draft; the first response already contains the form "
        "and setup output.",
        "Wait for attachment and the async result, edit the draft, then trigger another "
        "resource load.",
        "Setup remains 1 and mount becomes 1. Later resource updates preserve the draft "
        "and setup count.",
        "Initial HTML, staged graph adoption and browser mount are separate observable stages.",
        ("resources",),
    ),
    Lesson(
        20,
        "session-isolation",
        "Sessions and state boundaries",
        "Runtime and resilience",
        "Distinguish one learner's UI state from explicitly shared application data.",
        "Each browser session owns a graph. A `Signal` created inside the `app` callable "
        "belongs to that graph, so one learner's draft does not become another learner's "
        "draft. A module-level `Signal` instead belongs to the process and is shared.\n\n"
        "Neither boundary is a business database. A process-local shared value does "
        "not automatically coordinate several workers, and an ordinary UI `Signal` does "
        "not promise saved account data after refresh.",
        "The two counters below are independent component instances inside one session. "
        "A second browser tab is a stronger isolation check: it constructs a different "
        "application graph. Increment one counter and edit the private draft, then compare "
        "a newly opened copy of this page.\n\n"
        "Choose intentional sharing only when the product needs it, then define ownership, "
        "authorization and concurrency. Keeping private state local is simpler than "
        "trying to remove accidental sharing after several users already depend on it.",
        dedent('''
            from pysx import Fragment, pysx, signal


            def counter(prefix: str = "") -> Fragment:
                count = signal(0)
                snapshot = count()

                def increment(_: object) -> None:
                    count.update(lambda value: value + 1)

                return pysx(t"""
                    button(id={prefix + "increment"} onClick={increment}): "Increment"
                    output(id={prefix + "live-count"}): {count}
                    p: "Snapshot: "
                    output(id={prefix + "snapshot-count"}): {snapshot}
                """)


            def app() -> Fragment:
                draft = signal("")
                first = counter()
                second = counter("second-")

                return pysx(t"""
                    label(for="private-draft"): "Private session draft"
                    input(id="private-draft" bindValue={draft})
                    output(id="private-echo"): {draft}
                    {first}
                    {second}
                    p: "Open this lesson in a new tab to construct another independent graph."
                """)
        ''').strip(),
        "`app` creates one private draft and calls `counter` twice. Each call owns its own count "
        "and initial snapshot. Another tab or a refreshed document constructs a fresh graph; it "
        "does not inherit either existing counter.",
        "Edit a private draft and increment an instance. Open this lesson in a new tab, "
        "then refresh it.",
        "The new tab and refreshed page start fresh; the existing open tab keeps its own "
        "current state.",
        "Make state boundaries a deliberate product decision and verify them with two sessions.",
        ("counter", "reconnect", "session_security"),
    ),
    Lesson(
        21,
        "reconnection",
        "Disconnect and reconnect",
        "Runtime and resilience",
        "Observe connection state and recover the server's current UI without replaying actions.",
        "A temporary network interruption does not necessarily mean the server lost "
        "the graph. Reconnecting can reattach to that existing owner and reconcile the "
        "current state. Meanwhile, server-owned background work may have progressed.\n\n"
        "The client reports disconnection and bounds its retry policy. It does not "
        "quietly accumulate an unlimited queue of clicks or promise to replay actions "
        "whose effects are uncertain. That would be a different consistency contract.",
        "Type a draft and increment the live count. Use browser network tools to block "
        "the WebSocket temporarily, then restore the connection. The status indicator "
        "comes from the actual client connection, not a demonstration-only `Signal`.\n\n"
        "The server clock deliberately keeps running. On a healthy reattach you should "
        "see its current total rather than a replay of every missed visual tick. After "
        "the retained owner expires, recovery may reconstruct or reset; the next lesson "
        "explains that separate boundary.",
        dedent('''
            import asyncio
            from pysx import Fragment, own_task, push, pysx, signal
            from uuid import uuid4


            def app(*, clock: bool = True) -> Fragment:
                draft = signal("")
                count = signal(0)
                ticks = signal(0)
                generation = uuid4().hex[:10]

                async def run() -> None:
                    while True:
                        await asyncio.sleep(0.5)
                        await push(lambda: ticks.update(lambda value: value + 1))

                if clock:
                    own_task("lesson-clock", run)

                def increment(_: object) -> None:
                    count.update(lambda value: value + 1)

                return pysx(t"""
                    label(for="recovery-draft"): "Delivered draft"
                    input(id="recovery-draft" bindValue={draft})
                    output(id="recovery-echo"): {draft}
                    button(id="recovery-increment" onClick={increment}): "Increment delivered count"
                    output(id="recovery-count"): {count}
                    output(id="recovery-ticks"): {ticks}
                    p(id="recovery-setup"): {generation}
                    p:
                        "Inspect data-pysx-connection on the root during interruption."
                """)
        ''').strip(),
        "The draft starts empty and the count at zero. The increment handler writes the count; an "
        "owned task publishes a clock tick every half-second. Transport reattachment repairs the "
        "existing graph without a second event queue. Built-in connection notices require --watch "
        "or --dev; otherwise inspect data-pysx-connection on the root in browser developer tools.",
        "Edit, increment and temporarily block the socket. Restore it without reloading "
        "the document.",
        "The root's data-pysx-connection attribute reports reconnecting/connected; built-in "
        "notices appear only with --watch or --dev. The delivered draft and count remain "
        "after reattach.",
        "Reconnect reconciles current state. Unsent actions are not a durable transaction log.",
        ("reconnect",),
    ),
    Lesson(
        22,
        "browser-recovery",
        "Browser-held UI recovery",
        "Runtime and resilience",
        "Recover compatible delivered UI after server loss while keeping refresh semantics clear.",
        "Server loss destroys the old graph. Ordinary browser-held recovery therefore "
        "reconstructs a new graph from a bounded encrypted capsule retained by the open "
        "document. It is a continuity feature, not restoration of Python objects.\n\n"
        "Only supported latest-delivered UI state can be restored. The host's key ring, "
        "current authentication session, route and compatibility checks still apply. "
        "An undelivered edit or uncertain action must not become an invented confirmed result.",
        "Use this lesson's draft/counter to practice delivered edits. For the restart "
        "exercise, run the recovery playground on a server you control and keep its tab "
        "open while restarting it with the same command and application-state directory. "
        "Compatible delivered state should return.\n\n"
        "Now compare a refresh and a new tab: both start fresh ordinary UI. Capsules "
        "are held in document memory, not a cross-device account store. Use application "
        "persistence for saved business records and explicit durable UI state when you "
        "need the contract in the following lesson.",
        dedent('''
            import asyncio
            from pysx import Fragment, own_task, push, pysx, signal
            from uuid import uuid4


            def app(*, clock: bool = False) -> Fragment:
                draft = signal("")
                count = signal(0)
                ticks = signal(0)
                generation = uuid4().hex[:10]

                async def run() -> None:
                    while True:
                        await asyncio.sleep(0.5)
                        await push(lambda: ticks.update(lambda value: value + 1))

                if clock:
                    own_task("lesson-clock", run)

                def increment(_: object) -> None:
                    count.update(lambda value: value + 1)

                return pysx(t"""
                    label(for="recovery-draft"): "Delivered draft"
                    input(id="recovery-draft" bindValue={draft})
                    output(id="recovery-echo"): {draft}
                    button(id="recovery-increment" onClick={increment}): "Increment delivered count"
                    output(id="recovery-count"): {count}
                    output(id="recovery-ticks"): {ticks}
                    p(id="recovery-setup"): {generation}
                    p: "Keep this document open during interruption; refresh starts fresh."
                """)
        ''').strip(),
        "The empty draft and zero count are ordinary local signals, with an increment handler to "
        "produce delivered changes. The host supplies browser-held recovery. To restart a server "
        "you control, use the recovery playground below rather than stopping a public course "
        "server; its compatible draft/count behavior is the same.",
        "Restarting needs a server you control, so use a playground: scaffold "
        "`pysx-playground recovery`, run `pysx --app playgrounds.recovery`, save a draft "
        "and count by interacting, restart it with the tab open, then compare refresh.",
        "The open document restores compatible state after server loss; refresh starts a "
        "new draft.",
        "UI continuity reconstructs a new authorized graph; it does not persist business "
        "transactions.",
        ("browser_recovery",),
    ),
    Lesson(
        23,
        "durable-state",
        "Explicit durable state",
        "Runtime and resilience",
        "Declare a typed state root, validate its codec and rebuild it through a real store.",
        "Explicit durable UI state names the data that can cross a reconstruction "
        "boundary. A codec converts that typed root to bounded JSON and validates JSON "
        "on the way back. It must not serialize handlers, effects or arbitrary Python objects.\n\n"
        "A store retains versioned snapshots and ownership information. New graphs "
        "rebuild callbacks and resources from application code. A schema change needs "
        "a declared migration or an explicit unavailable/reset policy.",
        "The embedded lab is a real `DurableApplication` with a SQLite ownership store. "
        "Edit its declared draft, increment and keep the document open through a server "
        "restart. The setup marker changes while the committed root is reconstructed.\n\n"
        "The codec exercise beside it accepts bounded JSON data, not Python source. "
        "Try the invalid example and inspect the rejection. Persisting a UI draft still "
        "does not make an external payment or account write exactly-once; those operations "
        "need the application's own transactional design.",
        dedent('''
            import json
            from dataclasses import dataclass, replace
            from pathlib import Path
            from pysx import (
                DurableApplication,
                Fragment,
                HostConfig,
                SQLiteOwnershipStore,
                Signal,
                StateCodec,
                derived,
                project,
                pysx,
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

                def increment(_: object) -> None:
                    state.update(lambda value: replace(value, count=value.count + 1))

                return pysx(t"""
                    label(for="lab-draft"): "Committed UI draft"
                    input(id="lab-draft" bindValue={draft} maxLength={512})
                    output(id="lab-echo"): {draft}
                    button(onClick={increment}): "Increment declared count"
                    output(id="lab-count"): {count}
                    output(id="lab-setup"): {setup}
                """)


            def app() -> Fragment:
                return codec_sample()


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
        ''').strip(),
        "The codec validates the same LabState shape used by the embedded lab: a bounded draft, "
        "nonnegative count and bounded task records. The JSON controls call that decoder. The "
        "reduced durable view shows the draft, count and setup marker used in the restart "
        "exercise; create_lab wires it to a versioned SQLite store. The embedded lab also "
        "includes the later capstone controls.",
        "Reject the invalid codec sample here. For the restart, scaffold "
        "`pysx-playground durable`, run `pysx --app playgrounds.durable`, edit its draft, "
        "restart it and compare the setup marker.",
        "Committed declared state survives reconstruction, callbacks/resources are new "
        "and invalid data is rejected.",
        "Declare data and schema explicitly; rebuild behavior from trusted code.",
        ("durable_state",),
    ),
)
