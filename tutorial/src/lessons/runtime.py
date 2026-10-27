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
        "lifetime visible through pending, success, error and cancelled states. The loader "
        "runs independently, while its eventual publication belongs to the owning session.\n\n"
        "Await slow work outside a synchronous mutation turn. When background work needs "
        "to change signals, await push with a synchronous callback. Keeping that boundary "
        "explicit avoids holding a batch or a session mutation across arbitrary I/O.",
        "Reload replaces unfinished work and suppresses results from superseded generations. "
        "Cancellation is therefore more than showing a cancelled label: the old operation "
        "must lose the ability to publish over the current state.\n\n"
        "The local loader below deliberately waits long enough to interact with. Type "
        "a draft while it runs, trigger a replacement, and cancel another request. Your "
        "draft should remain editable and a cancelled result must not appear later.",
        dedent('''
            import asyncio
            from pysx import Fragment, derived, pysx, resource

            def app() -> Fragment:
                async def load() -> str:
                    await asyncio.sleep(0.05)
                    return "Ready"
                result = resource("message", load)
                text = derived(lambda: result().value or result().status)
                return pysx(t"""
                    p: {text}
                """)
        ''').strip(),
        "The resource key is stable within its owner. The derived text reads its current "
        "state, so pending and completed output share one live display.",
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
        "interface can still work. ErrorBoundary owns its content and a fallback. A "
        "failure retires the affected region instead of exposing a traceback to the page.\n\n"
        "ErrorInfo is deliberately redacted. It provides a safe message and correlation "
        "identity, while the cause stays in server diagnostics. Retry recreates the "
        "failed subtree; it does not undo an already committed business operation.",
        "Keep a healthy sibling outside the boundary. That makes the recovery scope "
        "observable: if its draft or count vanishes, the implementation reset too much. "
        "A component's local state may correctly restart when its own failed lifetime ends.\n\n"
        "The live panel throws a real handler error. Use the sibling while the fallback "
        "is visible, then retry. The fallback's retry callback is owned by the current "
        "boundary generation and should not revive an older discarded instance.",
        dedent('''
            from pysx import ErrorBoundary, ErrorInfo, Fragment, pysx
            from collections.abc import Callable

            def app() -> Fragment:
                def content() -> Fragment:
                    return pysx(t"""
                        p: "Healthy content"
                    """)
                def fallback(info: ErrorInfo, retry: Callable[[], None]) -> Fragment:
                    return pysx(t"""
                        p: {info.message}
                    """)
                Boundary = ErrorBoundary(content, fallback)
                return pysx(t"""
                    Boundary:
                """)
        ''').strip(),
        "The boundary receives functions, not eagerly built alternatives. Its fallback "
        "has safe ErrorInfo and a current retry capability.",
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
            from pysx import Fragment, on_mount, on_setup, pysx, signal

            def app() -> Fragment:
                setups = signal(0)
                mounts = signal(0)
                on_setup(lambda: setups.update(lambda n: n + 1))
                on_mount(lambda: mounts.update(lambda n: n + 1))
                return pysx(t"""
                    p: "Setup " {setups} " | mount " {mounts}
                """)
        ''').strip(),
        "on_setup belongs to the server lifetime and on_mount waits for browser confirmation. "
        "They count two distinct moments, not two names for initialization.",
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
        "Each browser session owns a graph. A Signal created inside the app callable "
        "belongs to that graph, so one learner's draft does not become another learner's "
        "draft. A module-level Signal instead belongs to the process and is shared.\n\n"
        "Neither boundary is a business database. A process-local shared value does "
        "not automatically coordinate several workers, and an ordinary UI Signal does "
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

            def app() -> Fragment:
                private_draft = signal("")
                return pysx(t"""
                    input(bindValue={private_draft})
                    p: {private_draft}
                """)
        ''').strip(),
        "The constructor is inside app, so it runs once for each new graph. Moving it "
        "to module scope would change the ownership and isolation of the same markup.",
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
        "comes from the actual client connection, not a demonstration-only Signal.\n\n"
        "The server clock deliberately keeps running. On a healthy reattach you should "
        "see its current total rather than a replay of every missed visual tick. After "
        "the retained owner expires, recovery may reconstruct or reset; the next lesson "
        "explains that separate boundary.",
        dedent('''
            from pysx import Fragment, pysx, signal

            def app() -> Fragment:
                draft = signal("Keep this draft")
                count = signal(0)
                return pysx(t"""
                    input(bindValue={draft})
                    p: {count}
                """)
        ''').strip(),
        "Ordinary local state is sufficient for a live graph. Reconnect policy belongs "
        "to the host/client transport, not a second queue hidden in this component.",
        "Edit, increment and temporarily block the socket. Restore it without reloading "
        "the document.",
        "The real status reports reconnecting/connected; the delivered draft and count "
        "remain after reattach.",
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
        "Use this lesson's normal draft/counter, keep the tab open, stop the tutorial "
        "server and start it with the same command and application-state directory. The "
        "browser should report reconstruction and restore compatible delivered state.\n\n"
        "Now compare a refresh and a new tab: both start fresh ordinary UI. Capsules "
        "are held in document memory, not a cross-device account store. Use application "
        "persistence for saved business records and explicit durable UI state when you "
        "need the contract in the following lesson.",
        dedent('''
            from pysx import Fragment, pysx, signal

            def app() -> Fragment:
                draft = signal("")
                count = signal(0)
                return pysx(t"""
                    input(bindValue={draft})
                    p: "Delivered count " {count}
                """)
        ''').strip(),
        "There is no special recovery wrapper around these local signals. Compatible "
        "ordinary state participates in the host's default browser-held recovery policy.",
        "Restarting needs a server you control, so use a playground: scaffold "
        "pysx-playground recovery, run pysx --app playgrounds.recovery, save a draft "
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
        "The embedded lab is a real DurableApplication with a SQLite ownership store. "
        "Edit its declared draft, increment and keep the document open through a server "
        "restart. The setup marker changes while the committed root is reconstructed.\n\n"
        "The codec exercise beside it accepts bounded JSON data, not Python source. "
        "Try the invalid example and inspect the rejection. Persisting a UI draft still "
        "does not make an external payment or account write exactly-once; those operations "
        "need the application's own transactional design.",
        dedent("""
            from pysx import StateCodec

            def decode(value: object) -> str:
                if not isinstance(value, str) or len(value) > 512:
                    raise ValueError("Expected a bounded draft")
                return value

            codec = StateCodec[str](lambda value: value, decode)
            assert decode("Draft") == "Draft"
        """).strip(),
        "Encoding is deliberately simple; decoding proves the expected type and bound. "
        "The real lab extends that pattern to a dataclass and a versioned host/store.",
        "Reject the invalid codec sample here. For the restart, scaffold "
        "pysx-playground durable, run pysx --app playgrounds.durable, edit its draft, "
        "restart it and compare the setup marker.",
        "Committed declared state survives reconstruction, callbacks/resources are new "
        "and invalid data is rejected.",
        "Declare data and schema explicitly; rebuild behavior from trusted code.",
        ("durable_state",),
    ),
)
