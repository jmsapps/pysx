# Hosting an application

See [application security](SECURITY.md) for output validation and the response CSP.
The [Linux deployment runbook](DEPLOYMENT.md) covers the TLS proxy, durable workers,
readiness, metrics, capacity, draining and backups.

Python 3.14+ is required. Install `pysx` and expose a callable returning a template
or Fragment, or an `Application`, in a module in your working directory:

```python
from pathlib import Path
from pysx import Application, HostConfig, StaticAsset, TransportLimits, native

def app():
    return native.H1("Hello from the server")

host = Application(app, config=HostConfig(
    base_path="/demo",
    assets={"/logo.png": StaticAsset.from_path(Path("logo.png"))},
    transport=TransportLimits(graphs=100),
))
```

Run `pysx --app user_app:host --port 8750` from that directory. The standalone
host serves HTTP and WebSockets on the same port. From a checkout, prefix this
command with `uv run --project .`. Programmatic serving uses
`async with host.listen("127.0.0.1", 8750)`.

For local edit-and-reload development, add `--watch`; see the
[development workflow](DEVELOPMENT.md) for source roots, candidate failure recovery,
browser reloads and the separate local proxy's limits.

Install `pysx[asgi]` to use `pysx --app user_app:host --asgi --port 8750`.
The optional host pins Uvicorn 0.54.0; the core package has no Uvicorn dependency.
For another ASGI server or middleware stack, expose `asgi_app = host.asgi()`.
The adapter implements ASGI 3 HTTP, WebSocket and lifespan messages. Configure
lifespan support so shutdown can drain connections and dispose pending graphs.
Importing the public APIs or checker starts no listener or event loop.

Healthy modern socket loss retains the live graph within the configured
`TransportLimits.resume_ttl` and `detached` limits. Resume needs worker affinity
and the same browser cookie, authenticated principal and login-session generation. Expiry/eviction/shutdown
dispose the graph; see [current-state recovery](PROTOCOL.md#current-state-resume).
The shipped browser retries within finite backoff/deadline bounds, rejects offline
events, and reports when an expired graph requires a fresh session. It never
replays business actions after uncertain delivery. Retry exhaustion offers manual
recovery. Normal `Application` documents also retain one encrypted capsule in memory
and reconstruct compatible ordinary UI state after graph loss. Refresh/new tabs start
fresh; capsules expire after 24 hours. Multiple workers share only the hosting key ring
and authentication authority, with a stable namespace. See [browser checkpoints](PROTOCOL.md#browser-checkpoints-v4).
[DurableApplication](OWNERSHIP.md) separately adds declared-state persistence and
leased worker ownership. Ordinary graphs retain no historical server snapshot store.

Both hosts share request handling, rendering, graph adoption and the connection
protocol. GET renders content immediately; HEAD returns metadata and disposes its
render. Anonymous reset uses a same-origin POST to the prefixed `/__pysx__/reset`
endpoint with `x-pysx-reset: 1`; the client exposes the `pysx:reset` window event.
Other methods return 405. Unknown static files return 404. ASGI request
bodies are unsupported. Assets contain bytes and an explicit MIME type; `from_path`
loads once at configuration time and infers MIME. Assets support HEAD, ETag and
If-None-Match (304), with configurable public `cache_seconds`. Personalized pages
are always `private, no-store`. Client JavaScript comes from the installed package.

`HostConfig.base_path` prefixes pages, assets and sockets. Routers without an
explicit base infer the request prefix. Use `host.path("next")` or
`current_request().base_path + "/next"` for links. Mounted ASGI applications can
instead receive their prefix from `scope["root_path"]`. Proxies must forward
WebSocket upgrades and preserve externally visible paths.

`current_request()` provides normalized headers, path/query, principal, base path
and mutable document metadata during rendering, events and cleanup. Configure
`HostConfig.principal` to resolve trusted request identity, or set
`scope["state"]["pysx_principal"]` in trusted ASGI middleware. The value must be a
string or None. A resolver may also return
`AuthenticatedIdentity(principal, session_id)`, where `session_id` is the current
trusted login-session ID or generation, checked against revocation by the authentication
service. ASGI middleware supplies the equivalent `pysx_authentication_session` field
alongside `pysx_principal` in trusted scope state. Never copy an unverified request
header into this field. Resolver-backed operation checks compare both fields, so
logout and same-principal re-login invalidate the old context. Principal-only adapters
remain supported for live sessions; they cannot authorize browser checkpoint recovery
after graph loss because the login-session binding is unavailable.

Both HTTP and WebSocket requests must resolve the same principal
for adoption. Authentication is supplied by the application. Configure an immutable
`SecurityPolicy` in `HostConfig.security` to enforce access:

```python
from pysx import SecurityPolicy

policy = SecurityPolicy(
    origins=("https://app.example",),
    require_principal=True,
    authorize=lambda request, action, target: request.principal is not None,
)
```

Without explicit origins the hosts accept only loopback peers and loopback Host
names; this preserves local development. Explicit origins enable remote hosting.
WebSocket upgrades then require an allowed Origin. Duplicate/malformed origins and
Host headers, opaque origins, credentials in origins and unsupported forwarding
headers fail closed before graph allocation. Origin is a browser CSRF boundary,
not an identity provider. Nonbrowser clients must supply the same allowed Origin
and application authentication. The authorizer must return exactly `True`; an
exception produces a safe denial. HTTP, websocket, adoption, resume, event,
resource, command, dom_reply, repair and reset are operation names. Targets are paths,
handler IDs, resource keys or outgoing message types. Use `require_access(action,
target)` inside a session for additional business service checks. Resource access
is checked before loading and before publishing a successful value. The principal
callback is rechecked before connected operations; a changed identity ends access.
Trusted middleware identities require the authorizer to check their live validity.

Call `host.revoke(principal)` on logout or authorization revocation. It disposes
pending, connected and detached graphs and invalidates their credentials; an active
socket closes without further private output. The application must also invalidate
its authentication credential so subsequent requests cannot authenticate again.
This operation is local to the application's worker.

Configure `SecurityPolicy.trusted_proxies` with exact peer IPs or CIDR networks to
accept single-valued `X-Forwarded-Host` and `X-Forwarded-Proto`, plus `X-Forwarded-For`
and `X-Forwarded-Port`, which are ignored. Every other forwarded header is rejected,
as is any forwarded header from an untrusted peer. Preserve the original peer at the adapter; a proxy must strip
incoming forwarded headers before setting its own. Paths/base paths must be
preserved explicitly; forwarded path prefixes are unsupported. ASGI servers should
disable their independent proxy-header rewriting so this policy sees the real peer.

The browser identity cookie is HttpOnly, SameSite=Lax and optionally Secure; it is
an ownership binder, not authentication. Application auth cookies should also be
HttpOnly/Secure with an appropriate SameSite policy. Adoption handles remain in
the private rendered document and resume handles in browser memory. The client
stores neither in localStorage and never sends resume handles in URLs.

`startup` and `shutdown` are tuples of synchronous or async hooks. Startup runs
once; shutdown runs in reverse order. `render_capacity` (100) and `render_ttl`
(30 seconds) bound unadopted graphs. `mutation_capacity` (64) bounds a session's
queued mutations and `task_capacity` (1024) its owned tasks and resource loads.
`shutdown_timeout` (5 seconds) bounds shutdown, including hooks and cancelled tasks.
`secure_cookies=True` adds Secure to the browser cookie for
HTTPS deployments. `development=True` or CLI `--dev` enables source tracebacks
in server logs; client errors remain safe.

Ordinary host startup also loads a constant-size recovery key ring. Explicit durable
hosts keep their existing store/lease credentials and require no browser key ring.
A single ordinary host generates
it automatically, without import-time writes, under the application's hashed namespace
in `~/Library/Application Support/pysx` on macOS or `$XDG_STATE_HOME/pysx` on Linux
(falling back to `~/.local/state/pysx` when XDG is absent or relative). Keep this private
state directory across deployments. Directories are owned with mode 0700, key files
0600, and symlinks are rejected. Missing or unsafe configured keys fail startup.

For multiple hosts, set a stable `HostConfig.recovery_namespace` and mount the same
ring at `HostConfig.recovery_key_directory / "recovery.keys"` on each host. An explicit
directory is read-only to the framework and must already exist with mode 0700; its
regular key file may be 0400 or 0600. The bounded binary ring is `PSK1`, an 8-byte
key ID and 32-byte root key, optionally followed by an 8-byte retiring ID, 32-byte
retiring root and 8-byte unsigned big-endian retirement timestamp. Use platform
secret management to generate random roots and distribute the ring atomically.
Keep at most one issuing root and one retiring root; retain the latter for at most
24 hours plus 60 seconds after rotation. Restart workers after installing a new ring.
Losing keys loses the ability to decrypt their checkpoints. Keys contain no user
snapshots; encrypted state and browser publication are separate runtime facilities.

`production=True` validates secure hosting configuration and forbids development
tracebacks. `observe` accepts a callback for redacted `RequestEvent` values; bounded
`Application.metrics` counters contain operation/outcome totals. See the
[production deployment and telemetry contract](SECURITY.md#production-deployment).

`HostConfig.transport` is an immutable `TransportLimits`. Its defaults are 100
total graphs per worker (pending HTTP graphs, upgrades awaiting hello and attached
graphs share admission), 5 seconds for hello, 2 seconds each for send and close,
64 KiB inbound frames, 1 MiB outbound batches, 2 MiB session outboxes, 64 queued
events, 16 queued socket frames, a 32 KiB write high-water mark and 20-second
keepalive ping/timeout. All values must be finite and positive, integral where
required. Outbound batches must fit the outbox. Admission failure returns HTTP
503 or closes a socket with 1013; assets and the health probe remain available at
capacity. Adoption transfers an existing graph slot. The probe returns `ok` while
the application is open, without constructing a graph.

`health_path` (`/health`) is the probe path relative to `base_path`. It is matched
before the renderer, so an application that owns that route must relocate the probe
or set `health_path=None` to disable it. A non-None value must be absolute and must
not be `/`; static assets still take precedence over it. The probe answers before
the origin policy and the authorizer so that infrastructure can reach it; set
`health_path=None` when that disclosure is unacceptable. Static assets are served
under the origin policy but without the authorizer, which governs application routes.

Oversized inbound/outbound messages close with 1009. Outbox pressure or a send
deadline closes/expires that graph; required state is never silently dropped.
Graceful close has its own deadline, followed by abort. Shutdown stops accepting
connections, cancels owned work, disposes every graph and drains task cleanup.
The packaged standalone and optional ASGI CLI configure their socket limits and
portable signal handling. Generic ASGI servers must configure equivalent frame,
queue, keepalive, write and process shutdown limits; ASGI itself has no standard
TCP abort API. The packaged optional host exposes a trusted transport extension
to enforce abort after a stalled close. `detached` (50) and `resume_ttl` (60 seconds)
bound live graph recovery.

Adoption handles are single-use and bound to browser cookie and principal. Pending
graphs and connected sessions live in one process. Multiple workers require
affinity from HTTP GET through WebSocket attachment. An expired or wrong-worker
handle closes the socket; reload obtains a new render. Live resume retains graphs
in the same worker. Durable hosts use worker hints and a shared ownership store to
reconstruct a fresh graph after owner loss; live graphs themselves are never stored.
See the [ownership contract](OWNERSHIP.md) for routing, draining and upgrade policy.

Create database pools in startup hooks and close them in shutdown hooks. Read
through an async resource loader; commit writes through explicit application
services invoked from events. Publish external updates with session-owned `push`.
Keep authentication, authorization and persistence in those services; runtime
signal graphs are not implicitly serialized to a database. See
[resources and server push](RESOURCES.md) for lifetime and mutation ordering.
