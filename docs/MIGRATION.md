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

## Replacing browser scripts

The browser-local APIs introduced in 0.7.1 cover viewport reveals and connection
controls. Import `Reveal`, `DisconnectFor`, `browser`, `Dom` and `Router` from `pysx`.
These declarations execute through the framework client without application scripts.

```python
reveal = Reveal(
    visible_class="is-visible",
    root_margin="0px 0px -10% 0px",
    once=True,
)
connection = browser.connection()
status = connection.text(
    connected="Connected",
    reconnecting="Disconnected. Reconnecting…",
    fresh="Connection: fresh",
    stopped="Connection: stopped",
    disconnected="Connection: disconnected",
    default="Connecting…",
)
```

Use these values in a `pysx` t-string:

```text
section(behaviors={reveal}):
    "Content"
button(disabled={connection.not_connected} onClick={DisconnectFor(seconds=5)}):
    "Disconnect for 5 seconds"
p(role="status" aria-live="polite"):
    {status}
```

`behaviors` is a runtime directive on native and styled native elements. Rendering
validates the descriptor and emits `data-pysx-behavior`; do not rename the directive
to `data-behaviors`. Keep any `data-reveal` selectors, transition rules and stagger
delays your CSS still uses. `Reveal` adds a class; it does not supply the animation.
Newly patched elements are observed and removed elements release observations.
Reduced motion reveals immediately, both on initial load and when the preference
changes. A once-only reveal stays visible after leaving the viewport.

Connection text and flags remain browser-local while disconnected. They are not
server Signals. The five state names above are the supported mapping keys; unmapped
states use `default`. Explicit mappings preserve labels that previously interpolated
the raw state name. `DisconnectFor` accepts durations greater than zero and at most
ten seconds. Reinserted controls receive their local bindings during reconciliation.

For a nested lesson panel, create `panel = Dom().ref()`, pass it to
`Router(routes, scroll_container=panel)`, and attach it with
`main(ref={panel}): {router.view()}`. See [scroll and focus](ROUTING.md#scroll-and-focus)
for the responsive overflow policy, fragments and post-patch timing. History positions
are stored per entry, with a 128-entry document-local bound, rather than per article.
Reload clears them. Repeated visits to one article can therefore restore different
positions. Known history positions take precedence over fragment scrolling.

The `browser_behaviors` browser suite covers insertion/removal, keyed reordering, offline
controls both before and after a route change, reconnect, initial and changed reduced
motion, panel history restoration, and the document-scroll fallback when responsive CSS
drops the panel's overflow. The `fragment_scrolling` suite covers decoded late targets,
repeated/native/named anchors, back/forward restoring scroll and focus, missing targets
with a cross-route fallback, and cancellation of stale fragment scrolling. The
`browser_storage` suite covers preference storage and both cookie phases. All run in
Chromium, Firefox and WebKit.

These checks establish framework behavior. An application's own CSS transitions,
breakpoint layout, reset controls and route integration still need browser verification
against that application, and none of this diagnoses an application's HTTP rendering
failure.

### Theme persistence

`Themes.register`, `select`, `current`, `read` and `clear` manage the live render's theme
definitions and selection. Persisting a choice across visits composes two general
capabilities rather than a theme-specific feature: a cookie, which the server can read
before it renders, and browser storage, which survives independently of it.

Precedence is cookie, then stored value, then your default. Only the cookie is visible to
the initial HTTP render, so it decides the first paint and there is no flash of the wrong
theme. The stored copy is read once the browser attaches; when it disagrees with the
cookie — because the cookie expired or was cleared — it wins and the cookie is renewed.
An invalid value from either source falls back to the default rather than failing.

```python
themes = Themes()
initial = current_request().cookies.get("theme", "light")
themes.select(initial if initial in THEMES else "light")
```

Writing a choice sets both: the cookie so the next render paints correctly, and storage so
the choice outlives the cookie. A browser that refuses storage keeps whatever the cookie
chose, and the selection still applies to the live connection, because `Themes` is server
state rather than a browser read.

See [the protocol](PROTOCOL.md) for both cookie phases, the storage frame and their
limits, and the browser storage example for a complete working version alongside an
unrelated preference.
