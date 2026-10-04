# Reactive state

`signal(value)` stores a value. `effect(fn)` runs once immediately and tracks every
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

`structured(value)` owns a deep copy on creation and replacement, and returns a deep
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
