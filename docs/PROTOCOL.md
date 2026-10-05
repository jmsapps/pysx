# pysx wire protocol (v2)

Supersedes the counter-era `{"slots": {...}}` format, which could only replace
text by index.

## Messages

**server -> client, once per connection**

```json
{"t":"init","html":"<div ...>","css":".pysx-ab12cd { ... }"}
```

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
validated before mutation; the reset handler runs after those updates in one batch.
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
