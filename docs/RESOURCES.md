# Async resources and server push

Create `resource(key, loader)` inside a component to own one asynchronous value.
The loader returns an awaitable; it starts once for that component generation.
Repeated renders with the same key retain the resource. `reload()` cancels the old
request and starts a new version; only the latest version can publish a result,
even if a loader suppresses cancellation. Removing the component or disposing the
session cancels its work and rejects late completion.

`Resource[T]()` returns a frozen `ResourceState[T]` containing `status` (`pending`,
`success`, `error` or `cancelled`), `value: T | None` and safe `error` metadata.
`state` exposes a readable reactive signal. Use `derived(lambda: value().status)`
or `derived(lambda: value().value)` for projected UI fields. `cancel()` invalidates
the current request and publishes cancelled state while its owner remains live.
Errors enter the captured ErrorBoundary; without a boundary, the safe error state
remains observable. Exception text stays on the server.

Loaders run independently with reactive tracking cleared. Their signal reads
never become dependencies of the effect that launched them. Refresh explicitly
from an event or an owned synchronous effect when inputs change. Keep long I/O in
loaders; capture application inputs deliberately and commit persistence through
your own async database/API layer.

`await push(callback)` commits a synchronous application mutation as one settled
batch from session-owned async work. The callback cannot return an awaitable.
Timers enter the same mutation queue. Async event handlers hold the session
mutation owner while awaiting, so one dispatch stays a coherent unit. `own_task`
coroutines do not: they run alongside dispatch and commit through `push`, so a
long-lived task never stalls events. The mutation queue admits 64 entries and
owned tasks 1024, matching the per-scope resource bound; `HostConfig` tunes both
through `mutation_capacity` and `task_capacity`. Full admission fails explicitly
rather than creating unbounded work. Writes from another active mutation owner or a disposed session
are rejected; direct synchronous signal writes remain supported while idle.

An independent outbox sends resource, timer and application-origin patches without
waiting for browser input. It shares the mutation lock with dispatch; failed-event
outcomes, their patches, navigation and mount announcements retain their order.
DOM-command replies continue on the separate receive path while an async handler
awaits the browser. Empty turns emit no patch and a mount generation is announced
once. Uncontained structural failure emits a safe result and closes the socket.

Loads may run while an HTTP graph awaits attachment. Their pending updates follow
the adoption acknowledgement. The HTTP handle's expiry cancels abandoned work;
disconnect and host shutdown dispose the session. This retains one graph on one
worker and does not reconstruct state after a disconnect.
