# Local error recovery

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
their fallback patches enter the session outbox. The current host sends those
patches on the next dispatch. Independent background push and async resource
scheduling require a host integration that drains the outbox. Cancellation during
disconnect releases resources without presenting an application error.
