# pysx wire protocol (v3)

Standalone and ASGI adapters share this protocol and session engine; see
[hosting](HOSTING.md) for configuration, prefix and process affinity requirements.

## Current-state resume

The host applies its origin/principal policy before upgrade/admission. HTTP access
denial returns 403; an accepted socket's denied event, chained handler, bundled
form edit, DOM reply or outgoing command closes with 1008 and a generic reason.
Authorization is rechecked against the retained principal before each operation.
Logout revocation expires pending, active and detached ownership; subsequent
adoption/resume cannot recover that graph. See [host security policy](HOSTING.md).

Modern attachment publishes a `resume` credential in `init` or `adopted`.
After healthy socket loss, request `/ws?protocol=3&resume=1&location=...` and
send `{"t":"hello","version":3,"resume":"credential","epoch":"epoch","state_rev":1}`.
The query is an admission hint; credentials travel only in the hello body.
Claims check browser cookie, authenticated principal, epoch and last revision.
Attached, expired, mismatched and already-used credentials close with 4004.
No invalid claim allocates or steals a graph. A successful claim rotates the
credential and sends an unconditional `repair` with complete current HTML/CSS,
next event sequence, routing state and the new credential. Retained native nodes,
keyed rows, focus, selection and imperative DOM capabilities survive repair.
The app and completed setup/mount callbacks do not run again.

Healthy modern graphs remain detached for at most 60 seconds, with at most 50
detached graphs inside the shared 100-graph worker budget. Monotonic expiry and
oldest-detached eviction dispose resources once. Detached mutations update the
current snapshot without retaining patches or notification queues. Outstanding
DOM requests fail on disconnect. Pressure/deadline, protocol close, host shutdown,
unrepaired render and application cancellation expire the graph. Legacy v2 graphs
are disposed on disconnect and have no live resume contract. Live recovery needs
affinity to the same worker; volatile graph loss requires a fresh app.
[Durable hosts](OWNERSHIP.md) can separately reconstruct explicitly declared state.

A normal peer close retains a healthy graph even if it overlaps an outgoing
update. Stopping a pending send during disconnect cleanup does not cancel the
application; resume repairs the current snapshot, including committed updates
the browser did not receive. Other failed sends still expire the graph.

The browser retains one set of application listeners during disconnect. It retries
automatically with exponential backoff from 250ms to 10s, 50–100% jitter, at most
eight retries, and a five-second attachment deadline. One timer/socket generation
owns recovery; page exit clears it. `ws`/`wss` follows the page scheme. Accessible
status explains disconnection and rejected edits. Managed actions are gated before
sequence/form/navigation bookkeeping; unsent and unacknowledged events are never
replayed. Repair restores authoritative control values and current navigation.
Browser outgoing buffered messages are limited to 64KiB; excess closes with 4009
and requires a fresh graph. Exhaustion offers a retry button or the
`pysx:reconnect` window event. Protocol failures stop retries and report the
distinct `stopped` state, which offers no retry button because neither it nor
`pysx:reconnect` can revive that page. Unavailable resume
(4004) explicitly starts a fresh session and visibly reports lost session state.

The client emits `pysx:disconnected`, `pysx:repair`, `pysx:rejected` (reason), and
`pysx:connection` (state, attempt, retryMs, fresh, timers, listeners, pendingBytes).
These bounded diagnostics contain no credentials. Fresh recovery clears old
imperative capabilities; same-epoch recovery retains them. Each tab owns its own
credential and session, even when browser cookies are shared.

## Declared-state reconstruction

Durable SSR includes private `data-pysx-recovery` and nonsecret `data-pysx-owner`
metadata. Attachment messages advertise optional `recovery` and `owner` fields.
The client includes `owner` as a URL routing hint and sends `recovery` only in the
hello body, alongside an existing `resume` credential when available. A
`recover=1` query hint requests reconstruction when no resume handle was received.
It is not a credential or proof of ownership. The host checks principal, browser,
authorization, store ownership and capacity before claiming state.

Unavailable live resume or an owner-lost adoption can fall back to declared-state
reconstruction using that separate credential. Success sends `init` with a new
epoch, `recovered:true`, authoritative HTML/CSS, a rotated live-resume handle and
the recovery credential/worker hint. Only this authenticated attachment transition
permits an already-attached client to accept a new `init` epoch. The client clears
prior DOM capabilities, listeners and sequence/revision state before installation.
Compatible elements are reconciled from the complete authoritative snapshot, retaining
node identity, focus and caret where possible. Per-control edit metadata is reset;
changed reference owners replace their nodes and cannot retain old capabilities.
Reconstruction installs the new graph's native control defaults and canonical
values, including uncontrolled checkboxes and selects. Same-graph repair retains
its existing reset defaults.
It displays that declared state was restored and uncommitted edits were discarded.
No old event or changed-control payload is applied to fresh handlers.

An explicit incompatible-schema fresh policy sends `init` with `fresh:true`, a new
credential and the normal visible fresh-session notification. `fresh` and
`recovered` cannot both be true.

A browser normally refuses a reconstructed `init` unless it already holds a recovery
credential, closing 4002. Development hosts send `development:true` on the first
`adopted`/`init` of a session; a browser that received it accepts a later
`fresh:true` reconstruction without a credential, so a volatile application can be
reconciled across a worker replacement instead of reloading the page. The exemption
is bound to a session that was established as a development session — it cannot be
claimed by a later message alone — and production hosts never send the field.

Unknown/revoked/mismatched credentials return 4004;
failed store/ownership/checkpoint operations return 1013. A live owner returns
4010 with only a bounded worker identifier as its close reason; the client updates
its routing hint and retries within the existing finite policy. A proxy resolves
hints against configured workers. Durable hosts require v3; v2 remains supported
by volatile hosts.

## Negotiation, revisions and event admission

The browser requests `/ws?protocol=3&location=...` and sends a bounded
`{"t":"hello","version":3,"edits":[]}` before attachment. With an HTTP
adoption handle, `edits` has the changed-control entries described below.
Unknown versions close with 1008. Direct peers omitting `protocol` or selecting
`protocol=2` retain validated v2 messages; they have no epoch/sequence guarantee.

Every v3 server message includes `version:3`, an opaque `epoch`, `base_rev` and
`state_rev` (base plus one). These revisions order every published frame and are
independent of form and navigation `rev`. The client applies only contiguous
frames from its epoch. A gap requests `{"t":"repair"}` with its envelope;
the server returns an unconditional current HTML/CSS snapshot and `expected`,
the next accepted event sequence. Owned identities reconcile this snapshot while
preserving surviving nodes and interaction state.

Client messages after hello include `version:3`, `epoch`, `state_rev`; events,
location observations and mount acknowledgements also include `seq`. Only the
next sequence is accepted, and the high-water mark advances before user code.
Older sequences receive `ack` status `duplicate`; gaps, mismatched epochs and
future revisions receive `gap`, `epoch` or `revision` followed by repair.
Successful dispatch receives `ack` with `seq`, `status:success` and `expected`;
failed/rejected dispatch receives a `result` carrying `seq`. A bounded 64-entry
acknowledgement cache never resets the high-water mark. Lost acknowledgements
and cache eviction cannot repeat a handler within the same epoch. External
business effects across graph loss/restart require application idempotency.

Incoming JSON is limited to 64 KiB, 16 levels of nesting and bounded collections.
Scalar/list top levels, nonfinite numbers, unknown message types/fields, incorrect
field types and unsupported versions fail before dispatch. Handler, binding,
mount and command IDs remain subject to their session ownership checks. DOM
commands/replies use the v3 envelope; the owned command payload API is unchanged.
Malformed server envelopes/ops make the browser close with application code 4002;
the browser WebSocket API reserves protocol close codes for the implementation.

The payload examples below omit epoch/revision envelope fields for readability;
all v3 frames require them. Explicit v2 compatibility payloads are labeled.

Stylesheet patches use `{"op":"css","v":"..."}` and replace the session's style
element via `textContent`. They precede the DOM operations that require those rules.
Dynamic rules and active themes belong to one session; removal releases owned rules,
while identical surviving rules remain deduplicated. CSS variables use ordinary `style`
attribute patches with empty variables removed from the serialized declarations.

Supersedes the counter-era `{"slots": {...}}` format, which could only replace
text by index.

## Messages

### HTTP rendering and attachment

An initial GET renders app content, settled setup state, CSS and document metadata
without a socket. Responses are private and uncached. The root has an opaque
`data-pysx-adoption` handle; a random HttpOnly `pysx_browser` cookie binds it to
the requesting browser, alongside the host-supplied principal. Bootstrap contains
no serialized signals, principal or callbacks. The external client opens `/ws`
with `adoption` and its complete local `location`, then sends
`{"t":"hello","version":3,"edits":[{"h":"binding-id","v":"current value","rev":1}]}`.
The v2 compatibility client sends `adopt` instead of `hello`.
Only changed controls submit edits, compared with their native HTTP defaults;
pristine bindings sharing a signal cannot overwrite an edited sibling. Only newly
checked radios submit their string values. All binding IDs and payloads are
validated before a batched, handler-free reconciliation turn.

The server reuses that graph and answers `{"t":"adopted","routing":false}`,
followed by any reconciliation patches and pending mount requests. The client
retains the existing root, controls, focus and caret, without reapplying defaults.
Equal binding echoes are suppressed; dependent content receives ordinary patches.
Setup runs once and browser mount still requires acknowledgement. Initial select
defaults are real selected options in HTTP HTML; textarea values are text content.

Handles are single-use. Pending renders default to a capacity of 100 and expire
after 30 seconds without requiring another request. Expiry/shutdown disposes their
graphs; failed and redirected renders retain none. Missing, expired or identity-
mismatched handles close with 1008 instead of constructing a second graph. This
requires HTTP and attachment to reach the same runtime. Direct sockets without a
handle retain the `init` contract below; adoption failure does not fall back to it.

Pending HTTP renders, upgrades awaiting hello and attached graphs share a default
100-graph worker cap. Graph reservation precedes awaited hello/upgrade work and
claim transfers the existing slot. Hello has a five-second deadline. The default
event queue is 64 messages, inbound frames 64 KiB, outbound batches 1 MiB and
session outboxes 2 MiB. Sends and graceful closes each have a two-second deadline;
a stalled close aborts the transport. Frame/batch overflow closes with 1009;
outbox pressure and send/admission limits close with 1013 and expire the graph.
Shutdown closes with 1001 and disposes all owned state within the host grace.
These limits are configurable through [hosting](HOSTING.md).

### Event outcomes and local recovery

An event outcome uses `{"t":"result","h":"handler-id","status":"error","ops":[],
"error":{"id":"opaque-correlation-id","owner":"opaque-owner-generation",
"phase":"handler","message":"This part of the application failed. Please try again."}}`.
Statuses are `success`, `error` (application failure), `invalid` (rejected payload)
and `stale` (unknown or removed handler). Only `error` carries an error description.
The client applies the ordered ops and synchronizes owned DOM handles before emitting
the window event `pysx:result`, whose detail has `handler`, `status` and `error`
(null for non-error outcomes). Consumers can clear their event error on success.

Writes already committed to signals are never rolled back. A failed dispatch must
deliver the ops that reflect those writes and any local fallback, then acknowledge
the failure. Form corrections keep their revision/echo rules. Pending ops and
unstarted callback awaitables are drained at the end of a dispatch. Healthy sibling
owners and other sessions remain independent. Mount/navigation messages follow the
result. Invalid protocol envelopes are distinct from application errors.

Errors use a fixed safe message, an opaque correlation ID, owner generation (null
for an uncontained failure), and phase: render, handler, computed, effect, cleanup,
mount or async. Raw exception text, tracebacks and local source paths stay on the
server. Local fallback receives the same safe metadata. Reset/retry clears the
boundary error and replaces only the failed ownership generation; removed-owner
failures and retries have no effect. A failing fallback escalates to its parent.
Cancellation during teardown is not an application error. Async callbacks belong
to their captured live owner. [Async resources](RESOURCES.md) commit through a
bounded session queue; an independent outbox pushes their patches and safe errors.

Both hosts emit result frames for failures and rejections. Successful v3 events
send any ordinary patches followed by an acknowledgement, including when nothing
changes. V2 compatibility events emit no frame when nothing changes. Boundary
reset clears its error on successful reconstruction.

An uncontained structural rendering failure that cannot produce a consistent
repair sends its safe error outcome and closes with code 1011; place an
ErrorBoundary around fallible rendering to enable local retry. The default server
logs only correlation ID and phase. `python -m pysx.server --dev ...`,
`Session(..., development=True)` and `render(..., development=True)` retain source
tracebacks in server logs. Client messages remain safe in either mode.

### Navigation

The WebSocket URL may carry `location`, an encoded local path/search/hash used to
construct the initial session. Invalid locations close with code 1008. `init` adds
an optional boolean `routing` to enable location observation. Browser history/hash
changes send `{"t":"location","url":"/path?query#hash","rev":1}`. Duplicate
popstate/hashchange URLs are suppressed. Routed events include `nav_rev`; a held
Link click also includes boolean `navigation` and its href in the event snapshot's
`value`. Native clicks leave `navigation` false.

Location messages enter the same bounded queue as events. Revisions must be
nonnegative integers and never decrease. Accepted URL changes update session-owned
signals. Resulting patches precede `{"t":"navigation","url":"/path","rev":1,
"mode":"push"}`. Modes are `push`, `replace`, `observe` and `external`; `observe`
acknowledges browser history without mutating it, and `external` assigns an HTTP(S)
URL. The client ignores navigation messages whose revision no longer matches its
latest intention. Link callbacks run before committing navigation and may explicitly
cancel it. A router built after `init` was sent announces itself with
`{"t":"routing","on":true}`, which enables observation exactly as the `init` flag
does; enabling twice has no effect. Queued navigation is flushed before every DOM
command and after a rejected payload or failed DOM command, so a turn that navigates
and then fails still commits the navigation it decided on. This contract covers the
current serialized connection. Resume restores current routing state after repair.

Navigation acknowledgement is also the scroll/focus commit boundary. Entry keys,
up to 128 saved positions/focus IDs, and bounded two-second fragment retries stay in
the browser. Later DOM patches can satisfy the current retry; URL/revision checks
cancel work for departed routes. The server exposes `RouteResponse` location,
pattern and status metadata for HTTP rendering hosts. The standalone host renders
route content for non-asset paths, including dotted route segments, using that
request's path/query. The browser's hash is reconciled during attachment.

### Component mount acknowledgement

After `init`, `adopted` or a `patch` commits an owner's markup, the server may send
`{"t":"mount","ids":["opaque-generation-token"]}`. The client processes DOM messages
in order, then replies `{"t":"mounted","ids":["opaque-generation-token"]}`. Each batch
contains at most 1024 tokens. The session accepts only its currently live generations;
duplicate, unknown and removed-owner tokens have no effect. A fresh owner receives a new
token even if it reuses a path or key. A surviving owner is acknowledged only once.
Server setup does not imply browser mount; these acknowledgements run synchronous
`on_mount` callbacks after commit. Resulting reactive patches follow the acknowledgement.
Acknowledgements enter the same bounded session work queue as events. An event rejected
for an invalid payload or a failed browser command still reports the markup it committed
and the mounts that markup owns.

### Owned browser commands

`Dom()` is created inside an app render. `dom.ref()` is interpolated as `ref` or
passed to a native constructor. `ref.handle()` captures an immutable `DomNode` for
that mount. Remounts receive fresh tokens; old handles raise `DomError`. Each query
and active-element read is limited to its owned root, including multiple app roots.
Node-property reads never return nodes outside that root. Queries, reads and writes
are asynchronous and must be awaited in an async callback.

Commands use `{"t":"dom","version":3,"id":...,"target":...,"root":...,"op":...,"args":...}`.
Replies use `{"t":"dom_reply","version":3,"id":...,"value":...}` or a bounded `error`.
The v2 compatibility command channel uses `version:1` instead.
Optional `revoked` lists invalidate removed node/listener handles. Requests use opaque
per-session tokens and IDs, a two-second deadline, at most 64 outstanding requests,
8192-character argument encoding and 16384-character result encoding. Each root has
at most 256 browser handles per session document; listeners are capped at 128.
Unknown/late replies are ignored. Removal and disconnect cancel requests; listeners
are removed when their target/root disappears. Healthy resume retains their ownership.
Browser reads
cannot run from an unconnected static renderer.

The serialized event worker awaits async callbacks while the receiver independently
processes command replies. Commands flush pending patches first, so initial mounting
and updated branches commit before their commands. Events queue in order (limit 64);
overflow closes the connection. This is command ordering, not a transport resume contract.

Supported operations are focus, blur, active-element, scoped query/get-by-id,
attribute get/set/remove, bounded property get/set, node-property reads, style
set/remove, selection get/set, scroll and measurement; element/text/fragment creation,
append/insert/remove; and owned element/window listener registration/removal.
Property names are value/checked/selected/disabled/tabIndex/textContent; node-property
names are parentNode/firstChild/nextSibling. Measurement returns a typed `Rect`.
Selection direction reflects browser normalization. Create supports HTML/SVG/MathML.

Structural, attribute, property and style mutations require `dom.ref(imperative=True)`
on an empty root. Its descendants belong to the imperative zone. Reactive content
uses ordinary rendering; commands cannot move nodes across owners or modify reactive
properties. Script-bearing elements/attributes, runtime metadata writes, executable
URLs and CSS URL/expression values are rejected. No command executes JavaScript.
DOM allocation and property results are bounded capabilities, not a blanket browser API.
Retained keyed elements keep ref tokens across value changes and reorder. Genuine
remounts invalidate captured handles; use fresh handles afterwards. Ancestor structural
patches leave the contents of retained imperative zones under their command owner.

Typed handlers created with `on_event` receive an immutable `BrowserEvent` snapshot.
The event message adds `event`, containing `type`, `handler`, target ID/value/checked,
key/code, alt/ctrl/meta/shift, repeat/composing, button/buttons, x/y, pointer_id/type,
related_target and submitter IDs. Missing optional fields use documented dataclass
defaults; unexpected fields, wrong types, nonfinite coordinates, mismatched handler/type
and strings over 4096 characters are rejected before callback execution. `value` carries
control contents rather than an identifier, so it is bounded at 1048576 characters.
Plain callbacks retain their existing value payload. Binding updates precede typed callbacks.

Rendered `data-pysx-policy-<type>` declarations carry prevent/stop, key filters, phase
and eligible-link policy. Their owning element also carries `data-pysx-typed`, the
space-separated list of the types it declares, so hydration registers delegation from
one marker instead of scanning every attribute of the patched subtree. Delegated typed
listeners exist only while the document still declares that type. Cancellation runs synchronously before network delivery.
Capture handlers run outermost first; bubble handlers run innermost first. Nonbubbling
events invoke bubble handlers only at their target. Stop applies to declared handlers
and browser propagation at the delegation point; native listeners earlier in the path
have already run. Custom event names are lowercase bounded markup tokens, registered
once per connection document while an owner needs them (128 distinct custom types).
Unused custom registrations and socket-owned listeners are removed on teardown.
Custom `detail` is deliberately not serialized.
Eligible links require an unmodified primary click, a relative same-origin URL,
no hash-only destination, no download and a current-window target. Absolute and
scheme-relative destinations retain native behavior. Keyboard filters do not cancel unmatched keys. Typed submit
handlers prevent native submission immediately while respecting validation.

**server -> client, once per direct connection without an HTTP handle**

```json
{"t":"init","html":"<div ...>","css":".pysx-ab12cd { ... }"}
```

A `patch` follows `init` immediately when component setup wrote a signal whose hole
had already been rendered, so the first frames always agree with the session's state.

**client -> server**

```json
{"t":"event","h":"h3"}            // click / submit
{"t":"event","h":"h7","v":"abc"}  // input / change; v is the value, or checked for a checkbox
```

**server -> client, after an event**

```json
{"t":"patch","ops":[ ... ]}
```

## Ops

| op | shape | client action |
|---|---|---|
| `text` | `{"op":"text","id":"0","v":"2"}` | `slot.textContent = v` |
| `html` | `{"op":"html","id":"4","v":"<button …>"}` | `slot.innerHTML = v` (conditional branches) |
| `attr` | `{"op":"attr","id":"e2","name":"class","v":"x"}` | set, or **remove when `v` is null** |
| `prop` | `{"op":"prop","id":"e2","name":"selectedValues","v":["a","b"]}` | update the selected properties of a multiple select's options |
| `list` | `{"op":"list","id":"6","keys":[…],"html":{…}}` | keyed reconcile (below) |
| `children` | `{"op":"children","id":"e2","range":false,"kind":"row","v":"<input …>"}` | reconcile changed children while retaining compatible descendants |

A patch carries only what changed. An empty op list is not sent.

Attribute names use canonical HTML spelling. Native boolean values are presence
or null; ARIA and enumerated booleans are the strings `"true"` and `"false"`.
For `value`, `checked`, `selected` and `muted`, the client updates the live DOM
property (null clears the property), preserving attributes that hold reset defaults.
Other attributes use set/removeAttribute.
Textarea initial values become escaped text content; HTTP select values are emitted
as selected option defaults. Direct socket initialization sets select properties
after its options have been inserted. Class patches carry the complete,
deduplicated class list. Marker IDs and list keys are HTML escaped and client
selectors escape their values.

`pysx/wire.py` defines the Python `TypedDict` contracts for these messages and ops.
Text and HTML values are strings; attribute values are strings or null; list keys and
markup-map keys are strings. Incoming JSON must be an object with `t` equal to `event`
and a string handler ID (an omitted ID resolves to no handler). Malformed JSON,
non-object values, other message kinds, and non-string handler IDs are ignored.

## Markers in rendered HTML

| hole kind | marker |
|---|---|
| TEXT | `<pysx-slot id="N">value</pysx-slot>` |
| COND | `<pysx-slot id="N">branch html</pysx-slot>` |
| ATTR | the owning element gets `data-pysx-el="eN"` |
| EVENT | `data-pysx-{type}="hN"`, type from the attribute name (`onSubmit` -> `submit`) |
| EVENT (typed) | additionally `data-pysx-policy-{type}` and `data-pysx-typed="{types}"` |
| LIST | paired `<!--pysx:list:T:start-->` / `<!--pysx:list:T:end-->` comments |

`T` is the lowercase hexadecimal UTF-8 encoding of the owner path. Rows use
the same pair with kind `row`; their path is `{list}:{key-segment}:`. ASCII
alphanumeric/underscore keys retain their spelling; every other key uses `~`
followed by lowercase hexadecimal UTF-8. This encoding is injective and keeps
delimiters, quotes and comment terminators out of paths. Keys are strings on
the wire; integer and string keys with the same spelling are duplicates.

Rows may contain zero, one or multiple roots, including text and void elements.
`data-pysx-key` on the first element is diagnostic metadata, never a boundary.
Slots inside tables, selects, SVG and MathML use comment pairs with kind `slot`
instead of custom elements. The client parses replacement markup using
`Range.createContextualFragment` at the actual parent, preserving constrained
HTML and foreign-content namespaces. Comment anchors contribute no elements
to CSS child selectors or accessibility trees.

## Keyed reconciliation

The server keeps retained row records, independently tracked builder dependencies,
child watchers and structural snapshots. Equal surviving values skip their builders.
Snapshots follow live child patches, so later value/shape changes compare against
the current DOM. Structural mounting precedes corrections to newly inserted descendants.
Readable text/properties/classes/styles and nested lists own independent effects;
their writes do not subscribe or reconstruct the enclosing row/list. Eager reads
inside a builder can rebuild that row, including same-route parameter changes.

Membership/order changes send the new key order and HTML only for newly mounted
rows. Same-key value changes rebind handlers/subscriptions and send affected
`text`/`attr`/`prop` operations, with no whole-row replacement. Structural changes
use `children`, addressed by `data-pysx-el` or a comment range (`range:true` and
`kind` equal to `row`, `slot` or `list`). The client preserves compatible nodes by
their owned marker identities, replaces changed tags/namespaces/ref generations,
and parses new children in their actual parent context. Nested live list ranges
reconcile independently. Ordinary iterable helpers and snapshots remain frozen.

The client then, in order:

1. creates or replaces ranges present in `html`,
2. removes complete ranges whose key is absent from `keys`,
3. reorders to match `keys`, moving all existing range nodes in order.

An unchanged item sends zero bytes and its DOM node is never touched, so focus,
selection and scroll survive a list update. This is the property keyed
reconciliation exists for, and it is what the acceptance test asserts.

## Handler identity inside lists

Handler ids are derived: `h{list}:{key-segment}:g{generation}:{n}` where `n` counts
handlers within one item. Retained targets keep their handler ids when their closures
change. Replaced native targets add a fresh mount suffix, and replaced branches
receive fresh path generations.
Rows retain their generation across reorder and same-key value changes; re-mounting
a removed key allocates a fresh generation, so removed IDs cannot address the new
owner. Same-key builders rebind their current closures. Removed rows dispose their
builder/child effects, handlers, styles, scopes, refs and resources.

Ref mount identity is stored beside markup by native element path and ancestor/tag,
namespace and component signature. Retained elements keep their UUID token even if rebuilding creates a new
ref object. Removed elements or changed structure revoke that token and mint a new
one, so previously captured handles stay stale after a genuine remount. Reordering
never revokes refs. Range moves restore focus, input caret/direction, DOM selection
and scroll only for surviving nodes.

## Echo suppression

Binding events carry an optional nonnegative `rev` edit revision. The server skips
an equal echo only for that binding's property. It preserves other attributes and
sends differing normalization results with the originating revision, even when the
normalized result equals the previous server value. The client ignores a correction
whose revision is older than its latest edit. Server changes without a revision
apply authoritatively. Equal values cause no DOM write; text corrections preserve
selection positions clamped to the corrected value's length.

Bindings use `data-pysx-binding`, `data-pysx-bind` and `data-pysx-bind-event`
markers. Modes are `value` (string), `checked` (bool), `radio` (shared string
value) and `selected` (array of strings). Multiple selections use property ops.
Composition holds intermediate edits and commits one final value.

An optional `after` handler ID runs after the binding in the same batch (for
example an `onInput` normalizer). Reset sends `edits`: an array of
`{"h":"handler","v":"initial","rev":3}` binding updates. All update payloads are
validated before mutation; the reset handler runs after those updates in one batch. A
typed reset handler uses the same message and adds its `event` snapshot, so it too runs
after the resynchronised bindings. A cancelled reset restores nothing and sends no
`edits`.
Native defaults remain unchanged by live patches.

Submit and reset handler payloads contain `entries` (successful-control
name/value pairs with duplicates retained), `valid`, and `submitter` (name/value
or null). Disabled controls are omitted. File entries carry name/size/type metadata,
not file contents. Native validation prevents invalid submission unless explicitly
disabled by the form or submitter. An `onInvalid` payload has `value` and `valid`.
Reset synchronizes enabled bound controls after the browser restores their defaults.
Delegated listeners are installed once; edit/composition state uses WeakMaps, and
hidden conditional bindings remove their server handlers and subscriptions.

## Deliberate limits

- Structural snapshots contain current mount markup; browser-edited properties
  remain authoritative until a live watcher sends a correction.
- One event produces at most one patch frame.
