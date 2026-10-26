# Move a UI to server-side reactivity

Start with the [installed application](QUICKSTART.md). The browser runs a small
DOM client; application Python runs on the server. Each connection owns a signal
graph, handlers, effects, resources and refs. Keep private UI state inside the app
callable. Shared business data belongs in an authenticated application service or
store with explicit concurrency rules. A module-level Signal shares one process's
state and is neither a cross-worker database nor an account isolation mechanism.

| Existing pattern | pysx equivalent | Contract |
|---|---|---|
| String HTML or client template | PEP 750 t-string passed to `pysx`; dynamic holes escape text | [Grammar](GRAMMAR.md), [security](SECURITY.md) |
| Local variable rendered once | `signal(value)` passed uncalled to a live hole | [Reactivity](REACTIVITY.md) |
| Getter in an eagerly interpolated template | Pass the Signal, or use `derived(lambda: ...)` | [Authoring](AUTHORING.md) |
| Reactive equality/boolean/collection operators | `eq`/`ne`, `all_of`/`any_of`/`not_`, `contains`/`length`; ordinary Python inside `derived` | [Reactivity](REACTIVITY.md) |
| Mutation of a copied object | Write through a typed projection or replace/update the root | [Reactivity](REACTIVITY.md) |
| Conditional component construction | Lazy `if`/`elif`/`else`, `when`, or template match branches | [Grammar](GRAMMAR.md) |
| Reordered array with index identity | Stable keyed `each` or explicit template `key` | [Ownership](OWNERSHIP.md) |
| DOM event object passed to application code | Bounded typed `BrowserEvent` with `on_event` | [Protocol](PROTOCOL.md) |
| Asynchronous preventDefault | Set the browser-local `on_event` policy before dispatch | [Protocol](PROTOCOL.md) |
| Global DOM lookup | Owned `Dom` refs and awaited capability commands | [Protocol](PROTOCOL.md) |
| Untracked effect, interval or fetch | `own_effect`, `own_timer`, `own_task`, `resource` and serialized `push` | [Resources](RESOURCES.md) |
| SPA pathname strings and relative path operators | `Router`, `Route`, `Link`, filesystem `./` and `../` paths | [Routing](ROUTING.md) |
| Client-memory restart recovery | Browser-held compatible UI capsule; current auth/key/route policy still applies | [Recovery](RECOVERY.md) |
| Persisted business transaction | Application storage; explicit durable UI state uses codecs/stores | [Persistence](PERSISTENCE.md) |
| Build-time template schema checks | Advisory diagnostics and strict generated Python typing; verify at runtime too | [Diagnostics](DIAGNOSTICS.md), [elements](ELEMENTS.md) |

Do not serialize Python callbacks, arbitrary DOM nodes or application secrets into
browser state. A repaired connection receives current state rather than replaying
unsent user actions. Refresh, new tab, open-document recovery and explicit durable
reconstruction have different boundaries; test each relevant journey. HTTP SSR
and browser attachment share one staged graph, while async resource updates arrive
later without rerunning setup. See [hosting](HOSTING.md).

Move one component at a time. Preserve semantic HTML and labels, verify keyboard
focus/ARIA and native form validation, and test two simultaneous sessions before
adding persistence. The [example guide](../examples/README.md) identifies focused
apps for each feature. Use [development hosting](DEVELOPMENT.md) and the
[language server](LANGUAGE_SERVER.md) while editing. Run deployment checks against
installed artifacts, then follow the [production runbook](DEPLOYMENT.md).
