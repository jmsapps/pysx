# pysx wire protocol (v2)

Stylesheet patches use `{"op":"css","v":"..."}` and replace the session's style
element via `textContent`. They precede the DOM operations that require those rules.
Dynamic rules and active themes belong to one session; removal releases owned rules,
while identical surviving rules remain deduplicated. CSS variables use ordinary `style`
attribute patches with empty variables removed from the serialized declarations.

Supersedes the counter-era `{"slots": {...}}` format, which could only replace
text by index.

## Messages

### Component mount acknowledgement

After `init` or a `patch` commits an owner's markup, the server may send
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

Commands use `{"t":"dom","version":1,"id":...,"target":...,"root":...,"op":...,"args":...}`.
Replies use `{"t":"dom_reply","version":1,"id":...,"value":...}` or a bounded `error`.
Optional `revoked` lists invalidate removed node/listener handles. Requests use opaque
per-session tokens and IDs, a two-second deadline, at most 64 outstanding requests,
8192-character argument encoding and 16384-character result encoding. Each root has
at most 256 browser handles per session document; listeners are capped at 128.
Unknown/late replies are ignored. Removal and disconnect cancel requests; listeners
are removed when their target/root disappears or the socket closes. Browser reads
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
List-item rerenders currently remount ref tokens. Because the token is part of the item
markup, every item of a list that holds refs differs on each render, so a single list
change replaces all of its rows and loses their focus, selection, scroll and edit
revisions. Finer retained ownership is a later rendering concern. Use fresh handles
after a remount.

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

**server -> client, once per connection**

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

A patch carries only what changed. An empty op list is not sent.

Attribute names use canonical HTML spelling. Native boolean values are presence
or null; ARIA and enumerated booleans are the strings `"true"` and `"false"`.
For `value`, `checked`, `selected` and `muted`, the client updates the live DOM
property (null clears the property), preserving attributes that hold reset defaults.
Other attributes use set/removeAttribute.
Textarea initial values become escaped text content; select initial values are
applied after its options have been inserted. Class patches carry the complete,
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
| LIST | `<pysx-list id="N">` wrapping items, each item carrying `data-pysx-key` |

## Keyed reconciliation

The server keeps, per list slot, the previous key order and the previous HTML
per key. On change it sends the **new key order** plus HTML **only** for keys
that are new or whose HTML differs.

The client then, in order:

1. removes nodes whose key is absent from `keys`,
2. creates or replaces nodes present in `html`,
3. reorders to match `keys`, moving existing nodes rather than recreating them.

An unchanged item sends zero bytes and its DOM node is never touched, so focus,
selection and scroll survive a list update. This is the property keyed
reconciliation exists for, and it is what the acceptance test asserts.

## Handler identity inside lists

Handler ids are derived, not allocated: `h{list}:{key}:{n}` where `n` counts
handlers within one item. The same item therefore keeps the same handler ids
across every re-render, so a click that arrives after a patch still resolves.
The server rebuilds a list's handler sub-table on each list render; ids for
surviving items are regenerated identically.

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

- Item-internal changes re-send that item's HTML rather than patching its
  individual slots. Keyed identity is preserved; sub-item slot granularity is not.
- One event produces at most one patch frame.
