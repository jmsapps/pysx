# pysx

**Reactive interfaces, written in Python.**

pysx brings the feel of a reactive web app to Python. Write your interface with
readable, indentation-based templates, keep state in signals, and let the page
update as that state changes.

Python runs on the server. A small JavaScript client connects the browser to your
application, sends user events, and applies the updates it receives.

## How it works

Each browser connection gets its own reactive state. Signals hold values, derived
signals compute from them, and effects track dependencies automatically. When a
user clicks a button or edits a field, Python handles the event and sends changes
back to the page.

Templates use Python 3.14+ t-strings to keep markup and live values together:

```python
from pysx import component, html, signal


@component
def app():
    count = signal(0)

    return html(t"""
        div:
            p: "Count: " {count}
            button(onClick={(lambda event: count.set(count() + 1))}): "Increment"
    """)
```

Changing `count` updates the displayed value. The browser applies targeted patches,
and keyed lists preserve unchanged rows so focus, selection, and scroll survive
updates.

## Build with pysx

- **Reactive state:** signals, derived values, batched updates, and writable views into structured data.
- **Readable interfaces:** t-string templates and composable, typed native elements.
- **Live forms:** two-way bindings, native validation, reset and submit handling, and caret-preserving server corrections.
- **Scoped styling:** typed styled bases, inherited rules, live CSS variables and isolated
  themes. See the [styling guide](docs/STYLING.md).
- **Editor support:** VSCode syntax highlighting and advisory template diagnostics.

## Explore

- [Examples](examples/README.md) — run the demos and explore reactive state and live forms.
- [Tests](tests/README.md) — run the test suite and quality checks.
- [Editor support](editor/README.md) — install the VSCode extension.

For the technical details, see the [template grammar](docs/GRAMMAR.md),
[native elements and bindings](docs/ELEMENTS.md), and [wire protocol](docs/PROTOCOL.md).
