# Hosting an application

Python 3.14+ is required. Install `pysx` and expose a callable returning a template
or Fragment, or an `Application`, in a module in your working directory:

```python
from pathlib import Path
from pysx import Application, HostConfig, StaticAsset, native

def app():
    return native.H1("Hello from the server")

host = Application(app, config=HostConfig(
    base_path="/demo",
    assets={"/logo.png": StaticAsset.from_path(Path("logo.png"))},
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
for adoption. Authentication and authorization belong to the application.

`startup` and `shutdown` are tuples of synchronous or async hooks. Startup runs
once; shutdown runs in reverse order. `render_capacity` (128) and `render_ttl`
(30 seconds) bound unadopted graphs. `mutation_capacity` (64) bounds a session's
queued mutations and `task_capacity` (1024) its owned tasks and resource loads.
`shutdown_timeout` (5 seconds) bounds waiting for cancelled tasks. `secure_cookies=True` adds Secure to the browser cookie for
HTTPS deployments. `development=True` or CLI `--dev` enables source tracebacks
in server logs; client errors remain safe.

Adoption handles are single-use and bound to browser cookie and principal. Pending
graphs and connected sessions live in one process. Multiple workers require
affinity from HTTP GET through WebSocket attachment. An expired or wrong-worker
handle closes the socket; reload obtains a new render. Transport resume and
cross-worker graph storage are not provided.

Create database pools in startup hooks and close them in shutdown hooks. Read
through an async resource loader; commit writes through explicit application
services invoked from events. Publish external updates with session-owned `push`.
Keep authentication, authorization and persistence in those services; runtime
signal graphs are not implicitly serialized to a database. See
[resources and server push](RESOURCES.md) for lifetime and mutation ordering.
