# Running examples

Run commands from the repository root with [uv](https://docs.astral.sh/uv/).
Python 3.14+ is required for t-strings; uv selects the project's interpreter and
prepares the environment without activation.

```sh
uv run --project . example run counter
uv run --project . example run todos
uv run --project . example run reactive_state
uv run --project . example run operators
uv run --project . example run forms
uv run --project . example run events
uv run --project . example run templates
uv run --project . example run
```

The last command lists available apps. Examples serve at http://127.0.0.1:8750.
Choose another port with `--port` or `PSX_PORT`:

```sh
uv run --project . example run counter --port 9100
PSX_PORT=9100 uv run --project . example run counter
```

Press Ctrl+C to stop the server. To use `example` directly, activate the environment:

```sh
source .venv/bin/activate
example run counter
```

The command is installed into the environment's bin directory. A new terminal
needs activation again, or you can keep using the `uv run --project .` prefix.

## Keyboard and focus

Run `uv run --project . example run events`. In the color chooser, Up/Down opens
the list and changes its active option; Enter selects it and Escape closes it.
Click an option to select it while keeping input focus. The event status shows
the key and modifier snapshot received by the server. Tab reaches the shortcut
row; Left/Right moves its single tab stop and browser focus. **Focus color chooser**
uses an owned asynchronous focus command. The chooser exposes expanded,
active-descendant and selected ARIA state. Each session has independent state.

Default cancellation happens immediately in the browser. Python callbacks receive
typed `BrowserEvent` snapshots through `on_event`; async callbacks await owned
`DomRef.handle()` commands. See [the protocol](../docs/PROTOCOL.md) for ownership,
imperative zones, supported operations and limits.

## Templates

Run `uv run --project . example run templates` for live template controls.
**Update heading** changes a live heading followed by two styled break siblings on
one row. The action is a later inline sibling, and each session owns its heading.
Reverse/add/remove groups, pick a nested row and switch through three branches.
Headings use `each_indexed` and ordinary Python assignments; nested `each` callbacks
retain their captured group after reorder. **Add child to first group** updates an
independent readable child source. `when` lazily builds the selected branch alongside
optional markup conditions and value matching. Local styled tags and cross-module
snapshot fragments use ordinary imports and bare component names. The initial
comprehension, iterable `each`, and bounded while snapshots stay fixed. Each browser
session owns its state.

## Reactive state

The `reactive_state` app demonstrates nested writable projections, multiplication
and batched computations. **Advance twice** shows a branch controlled by numeric
ordering and membership. **Increment first** updates the status and hides that
branch. **Edit a private snapshot** leaves the display unchanged, while **Rotate
list** changes the first positional projection. Collection length stays live.

The boolean helper card displays `all_of`, `any_of` and `not_`. Advance twice,
increment the first score, then **Reset count** to see each helper react to its inputs.
Each browser session owns its state.

## Operators

The `operators` app groups supported operations by Python's return-value rules:
arithmetic, indexing and ordering use operator overloads; `all_of`, `any_of`,
`not_`, `eq`, `ne`, `contains`, `length` and `concat` use named functions.
Use `derived(lambda: ...)` for other computations, including Python's
short-circuiting `and`, `or` and `not`. Signals have no `&`, `|` or `~` overloads.

## Live forms

```sh
uv run --project . example run forms
```

Open http://127.0.0.1:8750. Edit the required text, notes, checkbox, radio group,
single select and multiple select; their live values update below the controls.
Use Command/Ctrl to select multiple options. Each browser connection has its own state.

**Apply server values** updates every control. **Reset to initial values** restores
the browser defaults and live state. Empty the required text to see native
validation block submission; fill it and **Submit** to inspect enabled field pairs
and the submitter. Disabled fields are omitted.

The uppercase field demonstrates server normalization while preserving the caret.
The optional field can be hidden and restored. Text composition commits only its
final value. Press Ctrl+C in the terminal to stop the server.

The [native element API](../docs/ELEMENTS.md) documents typed construction and bindings.
The [verification guide](../tests/VERIFICATION.md) lists reproducible browser setup.

## Themes and CSS variables

```sh
uv run --project . example run themes
```

Open http://127.0.0.1:8750. Light/Dark switch this connection's named theme; Clear theme
resets its overrides. The preview inherits layout and borders through three styled levels.
Compact/Roomy change its padding, Local accent changes its border and user class, and
Reset variables removes both overrides to reveal inherited fallbacks. Open a second tab
to verify it stays independent. Controls support keyboard activation and narrow screens.
Press Ctrl+C to stop the server. See [styling](../docs/STYLING.md) for the public API.

## Callable components and lifetimes

```sh
uv run --project . example run composition
```

Open http://127.0.0.1:8750. The panel is an inherited styled callable that receives
caller-owned children. The tree recursively dispatches plain callable components.
Click a node or press Tab to focus the tree before using the keyboard.
Right opens a branch; Left closes it or moves to its parent. Up/Down moves through
visible nodes, Home/End reaches their endpoints, and Enter increments a node's local
activation count. Reverse roots preserves surviving state and DOM identity. Collapsing
a branch cleans its descendants; remounting starts their local activation counts fresh.
The counters distinguish server setup, acknowledged browser mount and cleanup.
Open a second tab to check session isolation. Press Ctrl+C to stop the server.
See [component ownership](../docs/GRAMMAR.md#component-ownership) for state and resource hooks.

## Routing and navigation

Run `uv run --project . example run navigation`. Open User 1 and increment its
counter, then switch to User 2: the route keeps its local count as the parameter
changes. Activity (`./activity`) descends into a nested route; Parent (`../`) returns
to its user, and User 3 (`../3`) selects a sibling. Plain child names also append to
the current pathname; `/` selects the root and `../../` climbs two levels. Files
demonstrates a wildcard.

Replace query changes the query without adding a history entry. Jump to details
scrolls and focuses the user section; browser Back and Forward restore known scroll
positions and focus. Cancel navigation leaves the current URL and view open. Another
tab starts its own session. The standalone example opens at `/`; HTTP deep-link
rendering is described by the [routing host contract](../docs/ROUTING.md).

Require sign-in changes a signal. An `effect()` observes that state and redirects to
`/login` with `router.navigate(..., replace=True)`, keeping history length unchanged.
Its path guard prevents repeated redirects. Continue clears the requirement and
navigates home; another tab keeps its own state. The effect is disposed with the app.

## Adding an example

Add `examples/<name>.py` with an `app` attribute; the registry discovers it
automatically, making `example run <name>` available.

Use shared layouts and controls from `examples/components/`, such as `Page`,
`Action` and `Title`. `page(t"""...""")` adds page-specific style overrides.
See [the shared theme](components/THEME.md) for palette, spacing and responsive layout.
