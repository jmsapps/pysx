# Worker ownership and declared-state recovery

`DurableApplication[T]` hosts a typed root-state factory through the same standalone
and ASGI adapters as `Application`. Its state codec and migrations follow the
[persistence contract](PERSISTENCE.md). Ordinary `Application` graphs are volatile,
but open documents can [reconstruct compatible UI state](PROTOCOL.md#browser-checkpoints-v4)
from browser-held capsules. That continuity has no persistent owner, lease, fencing
or globally latest writer guarantee.
See the [Linux deployment runbook](DEPLOYMENT.md) for a tested TLS proxy/two-worker
topology, operational probes, metrics and restart procedures.
The runnable [durable-state example](../examples/durable_state.py) demonstrates a
typed draft/counter root, fresh setup and async resources; its
[launch instructions](../examples/README.md#durable-state) cover local restart and
the two-worker topology.

```python
from pathlib import Path
from pysx import (
    DurableApplication, Fragment, Signal, SQLiteOwnershipStore, StateCodec, native,
)
from pysx.runtime.wire import JSONValue

def decode(value: JSONValue) -> int:
    if type(value) is not int:
        raise ValueError("expected integer")
    return value

def page(state: Signal[int]) -> Fragment:
    def increment(_event: object) -> None:
        state.set(state() + 1)
    return native.Div(state, native.Button("Increment", on_click=increment))

host = DurableApplication(
    page, initial=lambda: 0, codec=StateCodec[int](lambda value: value, decode),
    store=SQLiteOwnershipStore(Path("state.sqlite3")), application="counter",
)
```

Save this as `user_app.py` and run
`uv run --project . pysx --app user_app:host --port 8751`. `host.asgi()` and the
optional `--asgi` launcher preserve the same ownership rules. Supply `HostConfig`
for authentication, authorization, origins, secure cookies, assets and limits as
described in [hosting](HOSTING.md). The snippet is a local single-worker setup;
multiple workers additionally require an owner-aware proxy with the routing
behavior below. It is not an implicit cluster launcher.

Keep `application` stable across compatible code updates. Each process has a random
worker identifier by default. If overriding `worker`, give every concurrently
running process a unique URL-safe identifier of at most 64 characters. Worker
identifiers are routing hints, not authentication credentials.

## Identity, attachment and recovery

Every HTTP render creates an independent per-tab identity and a random recovery
credential. Private SSR markup carries the credential and worker hint; the browser
sends credentials only in hello bodies. Prepared principal and browser cookie bind
the stored identity. Recovery checks the same authorization hooks as live resume.
Unknown credentials and principal/browser mismatches allocate no graph. Durable
sessions require protocol v3 and a browser cookie; perform the initial HTTP GET
before opening a direct protocol client.

The page keeps its credential in memory. A page reload starts a new graph and
identity; this API does not invent a cross-page account-state lookup. Keep the page
open to recover its declared state across a worker restart. Application account
state across page visits belongs in an authenticated application service, or in
an explicit host-owned identity policy using the lower-level APIs.

Healthy same-owner reconnect uses the existing live-resume credential and retains
DOM nodes/resources. If that handle is unavailable, the separate recovery
credential permits reconstruction from committed declared state. Reconstruction
creates fresh handlers/scopes/resources and a new wire epoch, clears old DOM
capabilities and listeners, and discards unsent/unacknowledged edits without replay.
An accessible status distinguishes restored state from a fresh session.

Recovery may reuse an unclaimed pending HTTP graph. A detached graph whose resume
reply was lost is disposed before rebuilding from its committed snapshot. An
attached graph cannot be stolen by another connection using a copied credential.
Snapshots are never selected by a client-supplied principal.
The stored principal key is SHA-256 of `b"principal:"` plus the principal's UTF-8
bytes (surrogatepass); anonymous sessions use the distinct bytes `b"anonymous"`.
This encoding is stable across Python upgrades and does not depend on `repr`.

`host.revoke(principal)` atomically invalidates that principal's existing recovery
credentials in the application's shared namespace and releases their leases, then
revokes local graphs. Other workers fence those graphs before a queued mutation or
on their next renewal. New authenticated sessions get new credentials. Revoked
snapshots remain stored for retention/administrative policy but cannot be recovered
through their old credentials. Store failure makes revocation raise and closes
local graphs; callers must not report shared invalidation as successful when it
failed. Keep the application's authentication service authoritative for logout and
future authorization.

## One writer and bounded loss

`OwnershipStore` defines atomic acquisition, checkpoint, lease renewal, release,
route lookup and credential invalidation. `SQLiteOwnershipStore` is the reference
same-host implementation; `LeasedStateStore` binds it to a worker for lower-level
`DurableSession` use. Unbound checkpoint methods on the ownership backend reject
writes. Do not mix a raw state-store writer or direct database writes into a leased
application namespace.

Claiming an expired/unowned identity increments its owner epoch in the same
transaction as its snapshot. A checkpoint compares epoch/generation, verifies the
live lease and commits state plus transport high-water/revision before publication.
Renewals cannot revive expired or superseded epochs. A stale root write, queued
mutation or async completion cannot commit or publish. Lease release compares the
worker and epoch so stale shutdown cannot release its successor's ownership.

The default lease is three seconds, renewed in bounded batches every lease/3.
Hosted mutation turns and graph admission also check for overdue fleet renewal
before validating their owner, so a burst of synchronous work does not depend on
the periodic task getting an event-loop turn. Renewal never restores an expired
lease or replaces another worker's ownership; the ordinary ownership check still
rejects stale, expired or revoked writers before their callbacks run.
`lease_seconds` accepts 0.2–30 seconds; shorter leases require enough scheduling and
store headroom. All workers use the same host clock. Store transactions have the
adapter's finite timeout. No infinite retry loop runs inside an event. Expired
ownership or a failed checkpoint invalidates the graph and closes with 1013; the
browser's bounded reconnect policy applies. An unreachable store closes the socket
without invalidating the graph: renewal is the only authority that fences an outage,
and it does so once the leases it stopped renewing could have lapsed, never on the
first failed transaction. Another live owner redirects with 4010 and only its worker
identifier; a worker never redirects to itself, and reconstruction against the
current owner supersedes that worker's own graph for the credential. A proxy must
map hints to a configured worker allowlist, never to arbitrary destinations.

Admission reserves graph capacity before claiming a new writer. The tested
failover scenario uses 50 sessions on each of two workers with a 100-graph cap,
allowing the survivor to reconstruct all 100 sessions. Losing a worker when both
are already at 100 returns explicit admission rejection until replacement capacity
exists. Storage does not create RAM capacity. External business effects still
require application transactions/idempotency.

## Routing, draining and code updates

A proxy may distribute initial HTTP requests across healthy workers. SSR supplies
the selected worker hint, which the client includes in adoption/resume/recovery
URLs. Route an available known hint to that worker; after owner loss select a
healthy replacement. A successful reconstruction updates the hint. If a hinted
worker knows another live owner, its 4010 response directs the next retry there.
Neither recovery nor resume credentials belong in URLs, proxy logs or metrics.

`begin_drain()` refuses new HTTP renders/new graphs while allowing existing owners
and pending adoption to finish. Keep renewals running while draining. Then
`await host.close()` performs bounded socket/task shutdown and releases owned
leases. Abrupt process loss leaves leases to expire. Startup uses fresh process
state; it never unpickles old graphs or retains browser handles.

`schema` and the migration mapping belong to the application. Missing migrations
or newer snapshots default to explicit unavailable recovery with the old snapshot
unchanged. `fresh_on_incompatible=True` deliberately creates a new identity and
visibly reports lost session state; it never silently overwrites the incompatible
snapshot. Malformed state and failed migrations do not trigger this fallback.
Roll out compatible readers before changing stored formats; an older worker must
not be expected to read a newer schema it does not understand. Keep code/schema
compatibility and application idempotency in the deployment's upgrade policy.
