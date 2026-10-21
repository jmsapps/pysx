# Local development

For SDK code navigation and module ownership, see the [repository structure](ARCHITECTURE.md).

Install the package in a Python 3.14+ environment, then run from your application's
directory:

```sh
pysx --app my_app:app --watch --port 8750
```

`app` may be a rendering function or an `Application`. The ordinary `pysx --app`
command and repository example launcher continue to work without watching.
Watching has no additional dependencies and starts only with `--watch`.

The default source root is the current directory. Repeat `--watch-root PATH` to
include shared components or assets elsewhere. Python, `.pysx`, HTML, text template
and CSS files trigger reload on creation, content change or deletion. Hidden
directories, virtual environments, build output, node modules and symlinks are
excluded. Watching is bounded to 10,000 source files; narrow the roots if necessary.
A poll hashes a file's contents only when its modification time or size changed since
the previous poll, so an idle tree costs one stat per file rather than a full read. A
content change that preserves both the size and the modification timestamp — possible
only on filesystems whose timestamp granularity is coarser than the edit interval — is
not detected until something else about that file changes.
The poll interval defaults to 0.05 seconds (`--watch-interval`), with a 0.03 second
quiet period before loading an edit (`--watch-debounce`). An edit is loaded once the
quiet period elapses rather than on the following poll, so raising the interval no
longer multiplies the wait before a settled edit is applied. Local imports are loaded
fresh in a new process, conservatively invalidating all dependents.

The public listener stays bound while an isolated worker imports and renders the
candidate app. A successful edit replaces the worker and reconciles open browser
pages in place, preserving component-local state; see below. A failed import, initial render or
startup deadline keeps the last healthy worker and its handlers serving; a plain
text browser alert reports the failure. Repair the file to try again. Initial
failure serves an HTTP 503 until a valid edit arrives. Candidate startup defaults
to ten seconds (`--reload-timeout`). Interrupt the command to close the listener,
watcher, workers and WebSockets. Occupied ports fail before a worker is spawned.
On supported POSIX development hosts, workers own separate process groups so
descendants started by a rejected import are also terminated. Output draining
has its own shutdown deadline.

Application URL prefixes and authorization remain in effect. A candidate's
unauthenticated denial is not a startup failure; rendering errors are reported
when the authorized request runs. The proxy supports GET and HEAD and rejects
other HTTP methods with 405. HTML HEAD responses deliberately omit Content-Length,
which RFC 9110 permits. The proxy rewrites the development script URL in the GET
representation only, so the injected length is not derivable from a HEAD response,
and measuring it would require issuing a GET upstream — which would leave a pending
adoption that the upstream deliberately expires for HEAD. Non-HTML HEAD responses,
including the client script, report their true proxied length. The script URL is
rewritten exactly once per document. Responses are limited to 16 MiB and are not
cached in watch mode.

Planned replacement reconnects immediately, using development close code `4000`
and reason `development reload`. Only an established development client treats
that pair as planned; actual network/worker failures retain bounded randomized
backoff. Warm candidates validate the restored graph once. Failed validation
releases healthy traffic before the rejected process finishes bounded cleanup.

Stopping and restarting the watch command leaves open pages free to reconnect.
Startup notifications and generation numbers from a new supervisor do not reload
the page. A `DurableApplication` with the same identity and SQLite path recovers
its committed state through the normal recovery protocol. Ordinary component-local
state has no persistence across a complete server restart. Refreshing a page or
opening a new tab starts a new session. Within a running supervisor, an intentional
incompatible edit still performs the declared fresh reload.

## Component-local state preservation

Any application preserves component-local signal values across a source edit, with no
`DurableApplication`, codec or store. Each `signal(...)` call is identified by its
owning component path, source location and occurrence, and its value is carried to
the replacement worker in memory over the development control channel. Nothing is
written to disk and nothing crosses machines; this is a development convenience,
not persistence.

An ordinary refresh, an uncached page load and a new tab start from the current
initializers. Only an existing tab's authenticated live-resume token can claim
its hot-update snapshot. The handoff also checks browser, principal, epoch and
location, expires after 30 seconds and is consumed once. Tabs sharing cookies
keep separate values. Both anonymous signals and named `local_state()` slots
participate.

Compatible hot handoffs reconcile the authoritative new graph into the existing
DOM, retaining compatible nodes, focus and caret. They replace old handlers and
capabilities, reset form defaults to canonical values, and discard unsent edits.
A genuinely fresh fallback remains a fresh session with default state.

Before replacing a healthy worker, the candidate renders the captured state in
disposable graphs. These probes stage owned resources and dispose every graph;
they do not start subscriptions, effects or tasks. A restored-state render failure
keeps the healthy worker available until the edit is repaired.

Preservation is best effort and resets rather than guesses. Supported values are
exact JSON primitives, finite floats, lists and string-keyed dictionaries, nested
up to 32 levels. Tuples, non-string dictionary keys, subclasses, opaque objects,
cycles and nonfinite floats reset with a reason instead of changing their types.
A value also resets when its complete initializer call or initializer type changed,
when a call site is ambiguous (including loop-created signals), or when its slot
is absent or malformed. Initializer types are checked even when the value comes
from another variable and the call's source stays unchanged. Removed scopes and
slots are reported. Editing `count = signal(0)` to `signal(5)`
therefore shows 5, while an unrelated edit keeps the current count.

The `development` example explicitly composes a shared state panel; ordinary watch
applications and the separate inspector receive no preservation panel. The example
shows workspace aggregate counts of values actually restored, carried, dropped and
reset, with bounded reset reasons and source links. Runtime metadata never exposes
private slot values; the example separately binds its deliberately public sample count.
Its **Discard development snapshots** control clears pending development handoffs
and starts that page fresh; the endpoint is `/__pysx_dev__/cold`. This does not
reset other live tabs. This ordinary reactive example uses no persistence store. Run
`uv run --project . pysx --app examples.development:app --watch` and optionally add
`--inspect` for the separate inspector. Pass `--no-preserve-state` to disable
preservation for a whole session, which restores the previous behaviour of
reloading the browser on every successful edit.

In `examples/development.py`, increment from 0 to 1, edit its title and save:
the count stays 1. Increment to 2 and refresh: it returns to 0, as does a new tab.
Changing `count = signal(0)` to `signal(1)` instead resets the slot to 1 with an
initializer-change reason. Explicit persistence is a separate application choice,
demonstrated by `examples/durable_state.py`.

Watch mode responds to files written by either manual save or autosave. An editor's
autosave delay comes before watcher detection. The PYSX import-on-save action returns
promptly when analysis is pending and leaves imports unchanged for that save; it
does not hold the disk write waiting for workspace analysis. The default editor setup
runs that action on explicit saves; configuring it as `always` includes focus/window
autosave. VSCode's delayed autosave bypasses save actions.

## Declared-state hot replacement

An existing `DurableApplication` using a `StateCodec` and a stable
`SQLiteOwnershipStore` path opts into state-preserving replacement automatically.
Keep its application identity, store path and URL prefix stable between edits.
Use the [declared-state API](OWNERSHIP.md) to put every value that must survive in
the codec's root state. Component-local signals, old closures and running tasks
are rebuilt. No Python frame or arbitrary object is serialized.

The supervisor imports a candidate in a separate process, pauses forwarding and
new graph admission, and locks each retained graph's serialized mutation turn.
It validates current snapshots in isolated disposable graphs. These probes do not
activate owned effects, subscriptions, timers, tasks or resource loads. Failed
imports, migrations or state-dependent renders leave the old worker serving;
closing or timing out validation releases all acquired turns. A validated commit
retires the old process and its resources before forwarding to the replacement.
The existing authenticated recovery protocol then rebuilds each session from its
own declared state, installs new handlers/CSS and reconciles compatible DOM nodes.
Focus/caret survive compatible elements. Canonical control values and reset defaults
come from the new graph; uncommitted edits and old events are discarded.

Explicit schema migrations are honored. Incompatible state without a usable
migration, changed identity/store/prefix, and volatile apps use a declared fresh
reload. Watch workers also enable a fresh fallback for an incompatible recovery
outside the retained validation window; the normal production setting is unchanged.
The browser's recovery status distinguishes restored and fresh sessions.
Import/startup/setup code must be idempotent with explicit cleanup.
Codecs and migrations must be pure: validation runs them before adoption against
isolated snapshots, and actual reconstruction runs them against the live store.
An exception or invalid result from a migration rejects the edit.
Owned resource activation occurs after adoption; later failures use the normal runtime recovery
policy. Arbitrary file, network or other external effects of application setup
cannot be rolled back by process isolation.

Validation retains at most 256 session records for 30 seconds across worker
generations, covering rapid successive edits before a browser reconnects. It reads
the latest committed payload from the store rather than replaying a cached value.
Snapshot transfer is bounded to 16 MiB; exceeding a bound rejects the edit and keeps
the old worker. Narrow the development workload or wait for retention expiry.
Candidate import and validation each honor `--reload-timeout`, including the
candidate's initial render probe and every proxied request, so an application whose
render is slower than a couple of seconds is still adopted and still served.
Retirement has its own five-second process and one-second output-drain deadlines.
The listener's connection deadline covers a full worker swap plus a proxied request,
so a page refreshed while a replacement commits waits for the new worker instead of
losing its connection. Worker control uses
an unadvertised loopback listener with a per-worker random token and exists only
in explicit development mode.

## Inspector and source diagnostics

```sh
pysx --app my_app:app --watch --inspect
```

Open the “PYSX inspector” panel in the browser. It shows source links, per-session
owner/resource/task/handler counts and the last 256 metadata events. Writes,
dependency reads, effect runs, template/render work and queued/sent patch batches
have opaque identities and counts. Values are always `[redacted]`; no signal value,
closure, request header or codec payload is collected. Overflow drops oldest events
and increments the visible dropped count. Source links use a current user frame
when available; framework-only steps do not fabricate a user location.

The inspector endpoint is absent without `--inspect`. It requires ownership of a
pending/attached session through both browser cookie and current principal, then
calls the application's authorization hook with action `inspector` and the trace
ID as target. An application's explicit hook may deny this action. Separate
browsers/principals cannot inspect one another. Production hosts do not expose
the control endpoint or allocate trace buffers.

Source diagnostics use the same checker, diagnostic codes and UTF-16 ranges as the
CLI/editor. They describe saved source, including a rejected edit, while traces
describe the running application. Analysis executes no application code. One
separate process runs at a time and requests coalesce to the latest source version;
stale results cannot overwrite it. Analysis has the configured reload deadline,
at most 1,024 Python files, 4 MiB/file, 16 MiB total source/output and 256 diagnostics
per file. A limit or analysis failure is visible, rather than reported as a clean
result. Shutdown terminates and reaps the analysis process independently of app
workers. Analysis and the inspector asset are unused unless explicitly enabled.

## Development measurements

From a checkout:

```sh
uv run --project . python tests/dev_measurements.py --report .artifacts/dev/measurements.json
```

The focused harness creates 200 files / 500 components / 1 MiB source with a
100 KiB largest file. Its historical durable microbenchmark declares 20 active
imports; the ordinary-app browser arm imports all 199 leaf modules and renders
all 500 component definitions. Padding is recorded explicitly. Five edits of the
real counter and five large-fixture edits measure source write to RAF DOM text
observation in Chromium, retaining every sample, source hash, environment,
detection/quiet time, process startup/import, preflight, retirement, snapshot and
attachment timings. This endpoint does not qualify browser paint.

Use `--section reload` for the reload measurements alone. The default also records
paired disabled-capture and disabled-tracing measurements against the same source
with their respective guards removed. Outcomes and observer cleanup must match.
Capture qualification keeps its 5% budget; tracing qualification is separately
pending and its raw samples remain reported. Durable replacement also checks two
distinct protocol sessions and one surviving worker.

The focused limits are at most 5% disabled overhead and one second for replacement
plus protocol reconstruction. A failure is a failure; shared-host timing variance
does not waive a limit. Reports explicitly remain unqualified for release: the
reference runner, sustained load, full warmup/sampling and browser paint measurements
must be qualified separately. Static/browser acceptance does not prove these timing
limits.

Watch mode is for local development: it binds only loopback hosts, validates local
request origins, and exposes development status at `/__pysx_dev__/status` and a
push channel at `/__pysx_dev__/events`. The injected client subscribes to the push
channel so a replacement reaches the page without waiting for a poll, and falls back
to polling that status path whenever the channel is unavailable or closes. Both are
development-only and carry no application protocol messages. Watch mode cannot be
combined with `--asgi` or `--drain-signal`. Production hosts never load
the development poller. Application imports and startup hooks run in each candidate
process; make external startup side effects safe to repeat. Process isolation keeps
candidate Python globals separate, but does not roll back changes to external
services or files made by application code.
