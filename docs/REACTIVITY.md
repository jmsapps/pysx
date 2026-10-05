# Reactive state

`signal(value)` owns a deep copy of a value; reads and writes use detached snapshots.
Mutating a read never writes back. Values must support `deepcopy`; resources belong
outside state. Nested Signal payloads are rejected. `effect(fn)` runs once immediately and tracks every
signal read by its synchronous callback. `dispose()` stops future runs and releases
dependencies. Branch changes remove obsolete dependencies.

`derived(fn)` returns a read-only signal. A callable may read any number of differently
typed sources. Observed computations settle before effects; diamonds notify once with
the final value. Equal computed results do not notify their consumers. The optional
`equal` comparator receives two values and must be synchronous and side-effect free.

Computations are lazy until observed and detach upstream after their last observer
leaves. Detached reads recompute from current sources. `subscribe(callback, fire=True)`
returns an idempotent disposer. `fire=False` suppresses the initial callback. Callback
reads do not become subscription dependencies. Recursive computation raises
`RuntimeError`. Signal graph implementation fields are internal bookkeeping.

Effects and computations must be synchronous. Do not await or create asynchronous
tasks inside their callbacks: task creation inherits tracking context.

`with batch():` coalesces nested synchronous writes into one final notification turn.
Reads inside the block see current values. Server event handlers use this boundary.
Batches must not span awaits; access from another task or thread during a batch raises
`RuntimeError`. Writes remain committed if the body fails. Callback failures drain safe
siblings and surface as `ExceptionGroup`; a later turn remains usable. Feedback is
bounded to 1000 re-evaluations of any single node per flush, so a wide fan-out of
distinct observers is not mistaken for a loop. Effects may return a cleanup callback, called
before rerunning and on disposal. Cleanup ownership is cleared before invocation,
including when it raises.

`state[key]` selects an existing dictionary key; `state[index]` selects a list position.
Both return typed writable Signals, including nested forms such as
`profile["scores"]["first"]` and `rows[0]`. Use `.set(value)` or
`.update(lambda snapshot: replacement)` to write. For typed object fields, use
`state.project(getter, setter)`; the setter returns a replacement parent. Heterogeneous
record/tuple field inference and dynamic attribute names are not supported; use explicit
typed getters/setters for those shapes.

`structured(value)` remains a compatible legacy entry point. It owns a deep copy on creation and replacement, and returns a deep
copy on reads. Mutating a returned snapshot has no effect. Store copyable data values;
resources and handles belong outside structured state. Use `set(value)` or
`update(lambda snapshot: replacement)` to commit changes.

`dict_key(parent, key)` and `list_index(parent, index)` return typed writable
projections. `project(parent, getter, setter)` supports object fields and other lenses;
the setter receives a private snapshot and returns the replacement parent. Projections
compose for nested writes and remain Signals for rendering. Consumers track the selected
value; unchanged selections do not rerun downstream computations or produce patches.
List positions follow the current index after insert/delete/reorder, rather than row
identity. Missing keys/indices raise `KeyError`/`IndexError`; writes do not create missing
paths. A later valid parent replacement restores observed paths after a failed read.

Signals compare by identity with `==` and `!=` and are unhashable. Reactive payload
equality is `eq(a, b)` / `ne(a, b)`. Numeric and string ordering uses `<`, `<=`, `>`, `>=`
or `lt`, `le`, `gt`, `ge`; raw operands work on either side. Numeric ordering permits
mixed integer/float payloads. Boolean composition uses `all_of`, `any_of`, and
`not_`. N-ary helpers read every operand, including
when the first operand decides the output; unchanged outputs suppress notifications.
Empty `all_of()` is true; empty `any_of()` is false. Python `and`, `or`, `not`, `bool`
and chained comparisons coerce truthiness and raise with supported alternatives.

Prefer a computation for anything expressible as ordinary Python; it is the default
spelling and gives real `and`, `or`, `not` with real short-circuiting. Only the sources
actually read become dependencies:

```python
eligible = derived(lambda: enabled() and count() > 2)
blocked = derived(lambda: not eligible())
```

Reach for the named helpers when every operand must stay subscribed even though a
short-circuit would skip it:

```python
eligible = all_of(count > 2.5, contains(rows, first))
either = any_of(enabled, override)
blocked = not_(eligible)
```

pysx deliberately ships no `&`, `|` or `~` on Signals: those read as bitwise to a Python
reader, and an operator is only overloaded here when the protocol permits a faithful
reactive return and the Python meaning is unambiguous — arithmetic, indexing and
ordering qualify; `and`/`or`/`not`/`in`/`len` cannot and never will. The `operators`
example renders every supported spelling grouped by that rule, and `reactive_state`
shows the boolean helpers with controls that change their inputs.

`contains(container, item)` returns a reactive bool for lists, tuples, sets, frozensets,
strings and ranges; either operand may be a Signal. String membership means substring
membership. `length(value)` returns a reactive integer; `concat(a, b)` converts both
payloads to strings and returns a reactive string. `len(state())` and `item in state()`
are snapshots; `len(state)` and `item in state` are unsupported.
Python ranges remain half-open. `inclusive_range(a, b)` accepts integer or single-character
endpoints; `inclusive_enum_range(a, b)` uses Enum declaration order with inclusive endpoints.

Integer multiplication (`count * 2`, `2 * count`) preserves `Signal[int]`; float
multiplication preserves `Signal[float]`. Mixed numeric signals and other arithmetic use
`derived(lambda: ...)`, with normal Python promotion and no nested Signals. Plain called
Signals in a t-string are snapshots; pass the Signal itself or a derived computation for
live output. Operator results are lazy, read-only Signals with the same subscription and
disposal rules as `derived()`.
