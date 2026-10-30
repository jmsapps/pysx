<p align="center">
  <img src="assets/pysx-logo.png" alt="PYSX" width="260">
</p>

**Reactive interfaces, written in Python.**

PYSX brings the feel of a reactive web app to Python. Write your interface with
readable, indentation-based templates, keep state in signals, and let the page
update as that state changes.

Python runs on the server. A small JavaScript client connects the browser to your
application, sends user events, and applies the updates it receives.

## How it works

The initial HTTP response contains the rendered page. The browser attaches to that
same graph, preserving edits made before attachment. Each session gets its own
reactive state. Signals hold values, derived
signals compute from them, and effects track dependencies automatically. When a
user clicks a button or edits a field, Python handles the event and sends changes
back to the page.

Templates use Python 3.14+ t-strings to keep markup and live values together:

```python
from pysx import pysx, signal


def app():
    count = signal(0)

    return pysx(t"""
        div:
            p: "Count: " {count}
            button(onClick={(lambda event: count.set(count() + 1))}): "Increment"
    """)
```

Changing `count` updates the displayed value. The browser applies targeted patches,
and keyed lists preserve unchanged rows so focus, selection, and scroll survive
updates.

## Build with PYSX

- **Reactive state:** signals, derived values, batched updates, and writable views into structured data.
- **Readable interfaces:** t-string templates and composable, typed native elements.
- **Live forms:** two-way bindings, native validation, reset and submit handling, and caret-preserving server corrections.
- **Async applications:** owned resources, timers and server push update pages without another browser event.
- **Document continuity:** open pages restore compatible UI state after server loss. See [recovery](docs/RECOVERY.md).
- **Hosting:** shared standalone and optional ASGI serving, request context, static assets and lifecycle hooks. See [hosting](docs/HOSTING.md).
- **Scoped styling:** typed styled bases, inherited rules, live CSS variables and isolated
  themes. See the [styling guide](docs/STYLING.md).
- **Editor support:** VSCode syntax highlighting and advisory template diagnostics.

## Playgrounds

Copy runnable starter applications into your own project, then run them the ordinary way:

```sh
pysx-playground                        # all of them, into ./playgrounds
pysx-playground counter                # just one
pysx --app playgrounds.counter         # run it
pysx --app playgrounds.counter --watch # edit the file and watch it reload
```

`counter` is the smallest useful live application, `recovery` a draft that survives a restart
with the tab open, and `durable` declared state reconstructed through a fresh application
graph.
Re-running `pysx-playground <name>` restores that playground to its shipped source and
clears its state, so it is also the reset.

## Supported platforms

Python 3.14 or newer, on macOS and Linux. Ordinary changes use local verification;
release platform qualification runs separately. The reference
deployment topology needs a Linux container runtime; where a machine has not got one, the
suites that assert it report themselves as skipped rather than passing.

Windows is **not supported yet.** The library is not known to work there, and the test
suite does not pass on it; several components still assume POSIX process and signal
behaviour. Use WSL in the meantime.

## Explore

Start with the [quickstart](docs/QUICKSTART.md), work through the
[interactive tutorial](docs/TUTORIAL.md), or browse the [API index](docs/API.md).

- [Examples](examples/README.md) — run the demos and explore reactive state and live forms.
- [Tests](tests/README.md) — run the test suite and quality checks.
- [Editor support](editor/README.md) — install the VSCode extension.

For the technical details, see the [template grammar](docs/GRAMMAR.md),
[native elements and bindings](docs/ELEMENTS.md), and [wire protocol](docs/PROTOCOL.md).
