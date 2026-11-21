<p align="center">
  <img src="https://jmsapps.ca/assets/pysx-logo.png" alt="PYSX" width="260">
</p>

**Reactive interfaces, written in Python.**

PYSX brings the feel of a reactive web app to Python. Write your interface with
readable, indentation-based templates, keep state in signals, and let the page
update as that state changes.

Python runs on the server. A small JavaScript client connects the browser to your
application, sends user events, and applies the updates it receives.

## Install

Use Python 3.14 or newer and install
[pysx-framework from PyPI](https://pypi.org/project/pysx-framework/):

```sh
python -m pip install 'pysx-framework==0.7.1'
```

Or add it to a uv project with `uv add 'pysx-framework==0.7.1'`.
The Python import remains `pysx`. Follow the [quickstart](https://github.com/jmsapps/pysx/blob/v0.7.1/docs/QUICKSTART.md)
to run your first application.

## How it works

The initial HTTP response contains the rendered page. The browser attaches to that
same graph, preserving edits made before attachment. Each session gets its own
reactive state. Signals hold values, derived
signals compute from them, and effects track dependencies automatically. When a
user clicks a button or edits a field, Python handles the event and sends changes
back to the page.

Templates use Python 3.14+ t-strings to keep markup and live values together:

<img src="https://jmsapps.ca/assets/pysx-counter.svg" alt="Python counter example with PYSX template syntax highlighting" width="1048">


Changing `count` updates the displayed value. The browser applies targeted patches,
and keyed lists preserve unchanged rows so focus, selection, and scroll survive
updates.

## Build with PYSX

- **Reactive state:** signals, derived values, batched updates, and writable views into structured data.
- **Readable interfaces:** t-string templates and composable, typed native elements.
- **Live forms:** two-way bindings, native validation, reset and submit handling, and caret-preserving server corrections.
- **Async applications:** owned resources, timers and server push update pages without another browser event.
- **Document continuity:** open pages restore compatible UI state after server loss. See [recovery](https://github.com/jmsapps/pysx/blob/v0.7.1/docs/RECOVERY.md).
- **Hosting:** shared standalone and optional ASGI serving, request context, static assets and lifecycle hooks. See [hosting](https://github.com/jmsapps/pysx/blob/v0.7.1/docs/HOSTING.md).
- **Scoped styling:** typed styled bases, inherited rules, live CSS variables and isolated
  themes. See the [styling guide](https://github.com/jmsapps/pysx/blob/v0.7.1/docs/STYLING.md).
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

Python 3.14 or newer. The quality matrix has passed on Linux, macOS and Windows.
See [compatibility and support](https://github.com/jmsapps/pysx/blob/v0.7.1/docs/COMPATIBILITY.md) for tested versions,
deployment requirements and maintenance windows.

PYSX is available under the [MIT license](https://github.com/jmsapps/pysx/blob/v0.7.1/LICENSE).

## Explore

Start with the [quickstart](https://github.com/jmsapps/pysx/blob/v0.7.1/docs/QUICKSTART.md), work through the
[interactive tutorial](https://github.com/jmsapps/pysx/blob/v0.7.1/docs/TUTORIAL.md), or browse the [API index](https://github.com/jmsapps/pysx/blob/v0.7.1/docs/API.md).

- [Examples](https://github.com/jmsapps/pysx/blob/v0.7.1/examples/README.md) — run the demos and explore reactive state and live forms.
- [Tests](https://github.com/jmsapps/pysx/blob/v0.7.1/tests/README.md) — run the test suite and quality checks.
- [Editor support](https://github.com/jmsapps/pysx/blob/v0.7.1/editor/README.md) — install the VSCode extension.

For the technical details, see the [template grammar](https://github.com/jmsapps/pysx/blob/v0.7.1/docs/GRAMMAR.md),
[native elements and bindings](https://github.com/jmsapps/pysx/blob/v0.7.1/docs/ELEMENTS.md), and [wire protocol](https://github.com/jmsapps/pysx/blob/v0.7.1/docs/PROTOCOL.md).
