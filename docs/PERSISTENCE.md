# Explicit application state

Ordinary `Session` graphs declare `durability="volatile"`; `DurableSession` declares
`durability="durable"`. In-memory socket resume retains an existing
graph for a bounded time; it does not survive process loss. `DurableSession[T]`
provides an explicit lower-level reconstruction API. Use
[DurableApplication](OWNERSHIP.md) for hosted lease ownership and authenticated
browser reconstruction; a multi-worker deployment also requires owner-aware routing.

Declare one typed root `Signal[T]` and a `StateCodec[T]` containing application-owned
`encode(T) -> JSONValue` and `decode(JSONValue) -> T` functions. The render factory
receives that signal and builds fresh components, handlers, effects and resources.
Signal reads own their snapshots; replace state with `set`. Closures, graph objects,
sockets and browser handles are never serialized. The decoder must validate its
domain shape. Stored data never selects imports or Python callables.

```python
from pathlib import Path
from pysx import DurableSession, Fragment, Signal, SQLiteStateStore, StateCodec, StateIdentity, native
from pysx.wire import JSONValue

def decode(value: JSONValue) -> int:
    if type(value) is not int:
        raise ValueError("expected integer")
    return value

def page(state: Signal[int]) -> Fragment:
    return native.Div(state)

store = SQLiteStateStore(Path("application-state.sqlite3"))
session = DurableSession(
    page, initial=lambda: 0, codec=StateCodec[int](lambda value: value, decode),
    store=store, identity=StateIdentity("counter", "tab-id", "principal-id", "browser-id"),
)
```

`StateIdentity` separates application, session/tab, authenticated principal and
browser identity. These are trusted host inputs: authenticate and authorize them
before opening a session. Never use a client-supplied principal or guessable tab
identifier as a recovery credential. Supplying a request context does not replace
host identity validation. Each tab needs its own identity; browser cookies alone
are not a tab identity. No fallback to another identity is performed, and no
implicit reset of incompatible or malformed state occurs.

Supported values are null, booleans, integers with absolute value at most 2**53-1,
finite floats, strings, lists and string-keyed objects. The codec rejects other
values, nesting deeper than 16 levels, collections over 4096 entries and encoded
data over `max_bytes` (64 KiB by default; at most 1 MiB). Conversion functions are
trusted application code and must be bounded and side-effect free.

`schema` is a positive integer. `migrations={1: migrate_one_to_two, ...}` maps each
old version to a JSON-to-JSON conversion for the next version. Every intermediate
value obeys codec bounds. Missing migrations and newer snapshots raise
`IncompatibleState`, leaving stored state unchanged. The host must choose an
explicit user-visible unavailable or authorized reset policy.

Each immutable `StateSnapshot` atomically contains state, schema, identity, owner
epoch, checkpoint generation, event epoch, event sequence and outbound revision.
Claims and commits compare owner epoch and generation. Claiming fences the previous
graph; only a host ownership service may decide when takeover is safe. This is not
a live-owner discovery or routing service. Stale root writes and checkpoints fail.
`MemoryStateStore` is a thread-safe contract adapter with no restart durability.

`SQLiteStateStore` supports processes on one host using one local SQLite file.
Short `BEGIN IMMEDIATE` transactions provide atomic compare-and-swap with
`synchronous=FULL` and a bounded lock timeout (100 ms default, at most two seconds).
Network filesystems and multi-host storage are unsupported. Provision a private
host-owned directory and protect backups as application data. Store errors expose
generic outcomes: `StoreUnavailable` means the store could not be reached, and its
`RecoveryUnavailable` base means ownership was authoritatively lost. Other adapters
implement the atomic `StateStore` protocol and must preserve that distinction.

Serialized mutation turns checkpoint at completion, including server-origin
updates. Every socket frame checkpoints after assigning its revision and before
publication; HTTP output checkpoints before returning. Failure withholds the output
and clears pending output; socket recovery failure closes with 1013. An unreachable
store raises `StoreUnavailable` and leaves the graph intact and recoverable, because
the store has not observed ownership change hands; every other failure, including a
codec rejection, disposes the graph. Reopen the identity to rebuild from its last
committed state. Direct synchronous users must
call `checkpoint()` before treating a mutation as durable, or use a runtime turn.
Checkpoints contain actual declared state, including mutations made before a handled
application error. Handler exceptions do not provide application-state rollback.

Reconstruction creates and checkpoints a new wire epoch. A snapshot's high-water
metadata belongs to its recorded epoch; old events cannot run against fresh
handlers. The new epoch begins at sequence zero. Database, email and other external
effects remain application
transaction/idempotency responsibilities. A commit can succeed just before process
death or send failure; reconstruction retains that state even when the browser
never received its acknowledgement. No event replay is attempted.
