# Hosting an application

See [application security](SECURITY.md) for output validation and the response CSP.

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

Install `pysx[asgi]` to use `pysx --app user_app:host --asgi --port 8750`.
The optional host pins Uvicorn 0.54.0; the core package has no Uvicorn dependency.
For another ASGI server or middleware stack, expose `asgi_app = host.asgi()`.
The adapter implements ASGI 3 HTTP, WebSocket and lifespan messages. Configure
lifespan support so shutdown can drain connections and dispose pending graphs.
Importing the public APIs or checker starts no listener or event loop.

Healthy modern socket loss retains the live graph within the configured
`TransportLimits.resume_ttl` and `detached` limits. Resume needs worker affinity
and the same browser cookie and authenticated principal. Expiry/eviction/shutdown
dispose the graph; see [current-state recovery](PROTOCOL.md#current-state-resume).
The shipped browser retries within finite backoff/deadline bounds, rejects offline
events, and reports when an expired graph requires a fresh session. It never
replays business actions after uncertain delivery. Retry exhaustion offers manual
recovery; durable reconstruction and cross-worker storage remain application work.

Both hosts share request handling, rendering, graph adoption and the connection
protocol. GET renders content immediately; HEAD returns metadata and disposes its
render. Other methods return 405. Unknown static files return 404. ASGI request
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
string or None. Both HTTP and WebSocket requests must resolve the same principal
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
resource, command, dom_reply and repair are operation names. Targets are paths,
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
in the same worker; cross-worker graph storage is not provided.

Create database pools in startup hooks and close them in shutdown hooks. Read
through an async resource loader; commit writes through explicit application
services invoked from events. Publish external updates with session-owned `push`.
Keep authentication, authorization and persistence in those services; runtime
signal graphs are not implicitly serialized to a database. See
[resources and server push](RESOURCES.md) for lifetime and mutation ordering.
