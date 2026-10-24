# Application security

Authentication belongs to the host application. Use trusted middleware or the
principal callback and an explicit [host security policy](HOSTING.md). Origin
checks protect browser mutation, while authorization hooks govern business access.
Never construct Python templates or deferred code from network input. Templates
and static stylesheets are developer source; plain data should remain plain data.

Rendered text, keys, attributes and document metadata are escaped in their HTML
contexts. Attribute and element names must be markup identifiers. Event attributes
require callables. Ordinary `srcdoc` strings render as inert text; use
`TrustedHTML("<p>developer source</p>")` explicitly for developer-authored iframe
markup. The type accepts LiteralString for advisory source checking; never wrap
network values in it. Developer script templates remain available, while the HTTP
CSP blocks all inline execution. Navigation/resource URLs accept relative URLs and HTTP, HTTPS,
mailto and tel schemes; control characters, backslashes before a fragment and executable schemes
are rejected before initial or reactive output. Use external scripts supplied as
trusted static assets rather than embedding executable values in templates.

Dynamic flat CSS, inline style declarations, custom properties and imperative
styles are bounded data capabilities. They reject escapes, comments, markup,
braces, executable constructs and network-loading functions. Numeric/color,
variable/calculation, gradient, transform, filter and timing functions are allowed.
Use trusted declared stylesheets for CSS outside this dynamic capability, including
asset URLs. Custom properties additionally allow only one property value.

HTML responses are private and no-store. Each response has a fresh nonce for the
owned runtime stylesheet and sends Content-Security-Policy, nosniff and same-origin
referrer policy. Scripts and external static stylesheets load from the same origin;
the client evaluates no server payload. The stylesheet nonce permits private CSS
updates after adoption without unsafe-eval or inline scripts. `style-src-attr
'unsafe-inline'` permits the validated inline style/custom-property capability.
The policy disables base elements, objects and framing, bounds form submission to
the same origin and allows the host's websocket origin. Bootstrap ownership is
escaped DOM data rather than executable inline JSON. Styles, routes, graph state,
DOM capabilities and handler ownership remain per session.

## Production deployment

Set `HostConfig.production=True`, `secure_cookies=True`, HTTPS-only allowed origins,
`SecurityPolicy.require_principal=True` and an explicit authorizer. Production mode
rejects development tracebacks and non-HTTPS request contexts. Supply authentication
through verified application credentials or trusted ASGI middleware; neither a
principal header nor the framework browser cookie authenticates a user by itself.
When middleware supplies the principal, `HostConfig.principal` is not consulted; when
the callback resolves it, the identity is rechecked before each admission, event,
resource and DOM reply. V4 browser publications recheck the complete identity before
private output. Other outgoing commands use the authorizer; use
`Application.revoke(principal)` to retire a live graph. An
authorizer or principal callback that raises is reported as a server failure and
still denies.
For cookie-based authentication, validate credentials on HTTP and WS, use secure
HttpOnly cookies, revoke the credential in the application and call
`Application.revoke(principal)` at logout. Production browser ownership cookies
must be unambiguous opaque values; malformed or duplicate ownership cookies fail.

Browser-held recovery additionally binds the encrypted UI capsule to the current
login-session generation supplied through `AuthenticatedIdentity` or trusted ASGI
state. Principal-only adapters keep live behavior but cannot reconstruct capsules.
Same-principal re-login must use a new generation. Capsules contain no handlers or
service authority, and verification precedes application setup. The framework keeps
only a bounded host key ring and admitted live graphs, with no per-user recovery
denylist. Anonymous reset rotates its HttpOnly binder; a copied old binder together
with its valid capsule cannot be globally revoked without authoritative state.
See [hosting](HOSTING.md) for key management and [recovery](RECOVERY.md) for the
latest-delivered-state and business-effect boundaries.

The supported termination topology is HTTPS/WSS at a reverse proxy, a loopback
HTTP/WS backend and explicit `trusted_proxies` peer addresses. The proxy must strip
all incoming Forwarded/X-Forwarded-* headers, then set X-Forwarded-Host to the
external Host and X-Forwarded-Proto to https. X-Forwarded-For and X-Forwarded-Port
are accepted from a trusted peer and ignored; trust decisions use the actual peer
address. Any other Forwarded/X-Forwarded-* header, and any of them from an untrusted
peer, is rejected. Preserve the externally visible base
path and Origin, forward upgrades, and bound headers, handshake, tunnel queues and
timeouts. Do not expose the trusted backend directly to remote peers. The packaged
ASGI host disables its independent proxy-header rewriting so PYSX checks the actual
peer. Configure other servers equivalently. HTTPS certificate provisioning and
authentication are deployment-owner configuration, outside the framework.

The local test topology uses OpenSSL to create a one-day disposable certificate in
a temporary directory. Python verifies it against that fixture certificate; only
the browser fixture bypasses public CA validation. The bounded TLS proxy and
backend close their sockets/tasks on exit. The browser fixture removes its temporary
certificate/key directory; unit-test artifacts follow pytest's temporary-directory
retention and cleanup. These fixtures demonstrate termination, deep links, secure cookies, WSS
and resume without depending on external infrastructure.

## Operations and telemetry

`HostConfig.observe` receives immutable `RequestEvent` values with a generated
request ID, operation (`http` or `websocket`) and status. `X-Request-ID` exposes the
same HTTP correlation ID. Error logs carry generated error IDs/phase and the owned
request ID when available. The `pysx.access` structured logger and the application's
bounded success/denied/failure counters contain no identities, paths, query strings,
headers, credentials, event values or private state. Observer failures are contained
and redacted. The packaged ASGI host disables raw access logs, which could include
adoption query credentials. Keep independent reverse-proxy/server access/debug logs
redacted as well; never record Cookie, Authorization, adoption/resume or event data.

Use the authorizer as a rate-limit hook at HTTP/WS admission, event, resource or
business-service access. Return False when the application's bounded limiter
rejects a request; no graph is allocated on admission denial. TransportLimits
remain the independent hard capacity/queue/deadline limits. The health probe only
reports `ok` while the application is open, constructs no graph and reveals no
session/identity/metric details. So that load balancers can probe a backend that no
browser origin matches, it answers before the network policy and the authorizer;
disable it with `health_path=None` when that disclosure is unacceptable. The packaged
client asset is likewise served under the network policy but without the application
authorizer, which governs application routes.

Closing a websocket with 1008 means the request was denied. Session disposal during
shutdown or resume expiry closes with 1001 and is counted as an ordinary completion;
only `Application.revoke(principal)` turns a live session into a denial.
