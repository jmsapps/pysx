# Recovery

## Open-document continuity

Ordinary hosted components and signals recover compatible UI state automatically
after graph loss. The browser holds one bounded encrypted capsule in its document
closure. Refresh and new tabs start fresh; recovery does not write Web Storage,
IndexedDB or recovery payload cookies. The [ordinary draft example](../examples/browser_recovery.py)
and [launch instructions](../examples/README.md#browser-held-draft-recovery) demonstrate
restart recovery without a store, codec or required decorator.

Recovery first tries the retained live graph. Otherwise it authenticates the capsule
against today's browser, principal/login session, origin, namespace and route, and
constructs a new graph. Supported exact built-in values restore only when their
owner, call identity, initializer and root type match. See [state boundaries](GRAMMAR.md#component-ownership)
and [the bounded wire contract](PROTOCOL.md#browser-checkpoints-v4). Unsupported,
ambiguous or changed state restarts with defaults; the connection status reports
fresh or partially reset recovery. Aggregate overflow clears the previous token.

The capsule represents the latest state delivered with an accepted frame. It cannot
recover an unacknowledged action that never reached the server, guarantee globally
latest revisions, or make business effects exactly once. Old events and imperative
capabilities are discarded. Managed resources belong to the new graph; arbitrary app
setup may run for rejected candidates. Keep transactional/idempotency authority in
normal application services. [Explicit persistence](PERSISTENCE.md) and
[leased ownership](OWNERSHIP.md) provide separate contracts.

Anonymous `pysx:reset` rotates the HttpOnly browser binder and clears document
recovery. Authenticated logout must invalidate the login-session identity through
normal authentication. Copying an old anonymous cookie together with its capsule
cannot be globally revoked without an authoritative service; no per-user recovery
denylist is retained. Shared-host keys, rotation and legacy principal-only adapters
are documented in [hosting](HOSTING.md).

## Local error recovery

Create an `ErrorBoundary(content, fallback)` inside your app and render it as a
component, for example `Boundary:` where `Boundary` holds the instance. Content is
a zero-argument callable returning a Template or Fragment. Fallback receives an
`ErrorInfo` and a zero-argument retry callable, and returns the same kinds of markup.
The [recovery example](../examples/recovery.py) demonstrates the complete API.

Rendering, reactive computations, owned effects, event handlers, mount callbacks
and owned async callbacks route failures to the nearest live boundary. Fallback
releases the failed subtree's subscriptions, handlers, references, resources and
styles. A fallback failure escalates to its parent. Healthy siblings retain their
DOM and state. An instance mounts in one place at a time; removing it and rendering
it again starts a fresh lifetime with its error cleared.

Retry (also available as `boundary.reset()` or `boundary.retry()`) clears the
error and creates a fresh ownership generation. Its local state restarts. A retry
captured by an earlier fallback cannot reset a later generation or a removed
boundary. Reset does not undo application state or external side effects: remove
the cause of failure before retrying. Cleanup still attempts all callbacks when
one throws. Process-control exceptions and cancellation propagate normally.

`ErrorInfo` exposes safe `message`, `id`, `owner` and `phase` metadata. Production
clients and default logs never receive raw exception details. The standalone
server's `--dev` option and `Session(..., development=True)` retain tracebacks in
server logs; client messages remain safe. Use the correlation ID to match a
failure with its server log entry.

Failed events commit writes that already happened and send repair ops before the
error acknowledgement. Invalid payloads and stale handlers have separate outcomes.
Without a local boundary, handler failures can still deliver committed patches;
an unrepaired structural rendering failure closes the connection explicitly.
See [the wire contract](PROTOCOL.md#event-outcomes-and-local-recovery).

Async event handlers retain their captured owner across awaits. `own_task` and
`own_timer` hand failures to their live boundary and ignore departed owners;
their fallback patches enter the independent session outbox. Both shipped hosts
send background patches without another browser event. See [async resources](RESOURCES.md)
and [hosting](HOSTING.md) for scheduling and lifetime rules. Cancellation during
disconnect releases resources without presenting an application error.
