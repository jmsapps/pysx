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

## Adding an example

Add `examples/<name>.py` with an `app` attribute; the registry discovers it
automatically, making `example run <name>` available.

Use shared layouts and controls from `examples/components/`, such as `Page`,
`Action` and `Title`. `page(t"""...""")` adds page-specific style overrides.
See [the shared theme](components/THEME.md) for palette, spacing and responsive layout.
