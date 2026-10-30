# Public API index

Import these names from `pysx`. This index covers the supported root exports;
linked contracts define signatures, ownership rules, bounds and examples. The
generated native constructor annotations are also available to the type checker
and editor. Start with the [tutorial](TUTORIAL.md) or [installed quickstart](QUICKSTART.md).

## Rendering and templates

See [authoring](AUTHORING.md), [grammar](GRAMMAR.md) and [ownership](OWNERSHIP.md).

| Export | Use |
|---|---|
| `pysx` | Parse a t-string in its lexical scope and return a composable fragment; holes retain live sources. |
| `render` | Render a callable into a `Rendered` result; dispose the result when its lifetime ends. |
| `Fragment` | Composable rendered/template content returned by app and component callables. |
| `Rendered` | Rendered body, styles, watchers and owned resources; `dispose()` ends ownership. |
| `component` | Mark a callable for stable component identity and owned rendering. |
| `Component`, `Children` | Callable component and composable children typing contracts. |
| `Each`, `each`, `each_indexed` | Reactive collection regions; row callbacks receive values and optionally indices. Supply stable keys to retain record identity. |
| `when` | Lazily construct a conditional branch instead of eagerly evaluating both alternatives. |
| `Binding`, `Deferred`, `defer`, `defer2` | Explicit deferred evaluation/binding contracts for expressions that cannot be represented as an ordinary live source. |
| `bounded_while` | Bound a snapshot while expansion; it is not a reactive unbounded loop. |
| `PysxSyntaxError` | Positioned syntax failure for malformed markup. Static diagnostics remain advisory. |
| `TrustedHTML` | Explicit trust boundary for authored safe HTML; never wrap untrusted input. |

## Signals and operators

See [reactivity](REACTIVITY.md) for reads, writes, batching, identity and eager evaluation.

| Export | Use |
|---|---|
| `Signal`, `signal` | Typed scalar or structured reactive state; call to read, `set`/`update` to write. Uncalled sources remain live in holes. |
| `derived` | Lazily recompute a value from the sources read inside its callback. |
| `effect` | Observe dependencies immediately; return cleanup from its callback and dispose the returned observer. |
| `batch` | Coalesce synchronous writes into a settled turn; do not await inside it. |
| `Structured`, `structured` | Explicit structured-state type and constructor; nested writes retain the root's ownership. |
| `Projection`, `project` | Typed readable/writable view onto a structured path. |
| `dict_key`, `list_index` | Typed key and positional-index projections. |
| `eq`, `ne` | Reactive payload equality/inequality; Python Signal equality retains identity semantics. |
| `gt`, `ge`, `lt`, `le` | Reactive ordering helpers, also available as ordering operators. |
| `all_of`, `any_of`, `not_` | Reactive boolean composition; Python `and`/`or`/`not` cannot return reactive values. |
| `contains`, `length` | Reactive membership and collection length; Python `in`/`len` require primitive results. |
| `concat` | Reactive text concatenation from constant and readable pieces. |
| `inclusive_range`, `inclusive_enum_range` | Inclusive integer and enum ranges for membership and iteration. |

## Components and async lifetimes

See [ownership](OWNERSHIP.md) and [resources](RESOURCES.md).

| Export | Use |
|---|---|
| `local_state` | A named typed state slot retained by its component identity. |
| `on_setup` | Work at the component's initial server setup. |
| `on_mount` | Work when the browser confirms mounting; distinct from server setup. |
| `on_cleanup` | Release work when its owning component leaves or the session closes. |
| `own_effect` | Install a keyed effect with automatic subscription cleanup. |
| `own_subscription` | Acquire a keyed subscription and own its returned unsubscribe callback. |
| `own_task` | Start a keyed async task owned and cancelled by the component. |
| `own_timer` | Schedule a keyed owned timer; its mutation runs through the session runtime. |
| `push` | Await a serialized synchronous mutation callback from async background work. |
| `Resource`, `resource` | Owned async loader with reload, cancellation and stale-generation suppression. |
| `ResourceState` | Typed pending/success/error/cancelled state, result or redacted error. |
| `ErrorBoundary` | Local failure fallback and explicit retry/reset within an owned region. |
| `ErrorInfo` | Redacted failure identifier/phase; full causes belong in server diagnostics. |

## Elements, events and browser capabilities

See [elements](ELEMENTS.md), [protocol](PROTOCOL.md) and [security](SECURITY.md).

| Export | Use |
|---|---|
| `native` | Typed constructors such as `native.Input`, typed attrs and explicit custom-element/custom-attribute escapes. |
| `br`, `button`, `div`, `form`, `h1`, `input`, `label`, `li`, `nav`, `p`, `section`, `span`, `strong`, `ul` | Native element markers usable as styled bases and template bindings. |
| `BrowserEvent` | Bounded typed event snapshot including configured keyboard, modifiers and form data. |
| `EventHandler`, `on_event` | Typed callback plus immediate browser policy for keys, default and propagation. |
| `Dom`, `DomRef` | Create owned refs, then await operations on their mounted handles. |
| `DomNode` | Bounded owned browser capability; querying cannot escape the ownership boundary. |
| `DomListener` | Releasable owned browser listener capability. |
| `DomError` | Rejected/expired/invalid browser capability operation. |
| `Rect` | Typed geometry returned by an awaited browser read. |

## Styles

See [styling](STYLING.md). Literal definitions may be shared; dynamic definitions
and active theme state belong to their render/session.

| Export | Use |
|---|---|
| `styled` | Define scoped native or callable styles with inheritance and variants. |
| `StyledTag`, `StyledCallable` | Typed styled native-element and callable-component definitions. |
| `css` | Validate and return flat snapshot CSS text; live signals and unexpanded braces are rejected. |
| `stylesheet`, `global_style` | Declare reusable stylesheets or intentional global rules. |
| `Theme`, `Themes` | Normalized named theme values and session-local registration/select/clear state. |

## Routing

See [routing](ROUTING.md). Route state belongs to the matched branch; navigate
through the router or a `Link` to preserve normal browser exceptions.

| Export | Use |
|---|---|
| `Router`, `Route` | Declare and own exact, parameter, nested, wildcard and fallback branches. |
| `RouteState` | Live matched parameters and location passed to the branch. |
| `Location` | Parsed path/search/hash location value. |
| `Link` | Internal navigation with composed handlers and native anchor exceptions. |
| `NavigationEvent` | Navigation context including explicit cancellation. |
| `RouteResponse` | Route-level HTTP response/redirect contract. |
| `match_route` | Match a location against the declared route pattern. |

## Hosting and security

See [hosting](HOSTING.md), [development](DEVELOPMENT.md), [security](SECURITY.md)
and the [deployment runbook](DEPLOYMENT.md).

| Export | Use |
|---|---|
| `Application` | Serve one callable through HTTP/SSR, browser attachment, sessions and optional ASGI. |
| `HostConfig` | Host namespace, base path, budgets, assets, request/security hooks and recovery policy. |
| `StaticAsset` | Explicit static content or file with media type and caching policy. |
| `Document` | Request-owned title, language and document metadata. |
| `RequestContext`, `current_request` | Current request headers/path and host-owned identity/document context. |
| `AuthenticatedIdentity` | Principal with a nonempty authentication-session generation. |
| `SecurityPolicy`, `require_access` | Current host policy and explicit authorization check at a capability boundary. |
| `AccessDenied` | Deliberate rejection of an unauthorized request or capability operation. |
| `RequestEvent` | Bounded request telemetry without exposing secrets. |
| `TransportLimits` | Bounded transport admission, replay, heartbeat and detach budgets. |

## Explicit durable UI state

See [persistence](PERSISTENCE.md), [recovery](RECOVERY.md) and
[deployment](DEPLOYMENT.md). Ordinary browser-held recovery needs no durable
wrapper. Explicit durable state still does not replace business transactions.

| Export | Use |
|---|---|
| `DurableApplication`, `DurableSession` | Opt-in hosted durable state reconstruction and its session root. |
| `StateCodec` | Explicit versioned typed encoding/decoding of declared state. |
| `StateIdentity`, `StateSnapshot` | Bound state identity and versioned committed snapshot. |
| `StateStore`, `MemoryStateStore`, `SQLiteStateStore` | Durable-store protocol, local memory implementation and SQLite implementation. |
| `OwnershipStore`, `SQLiteOwnershipStore`, `OwnerLease` | Atomic ownership/fencing contract, SQLite implementation and current lease. |
| `IncompatibleState` | Declared state cannot be decoded under the current schema/policy. |
| `StoreUnavailable`, `RecoveryUnavailable` | Store failure or reconstruction unavailable under the current conditions. |
