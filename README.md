# pysx

## Server-side reactive UI for Python, templated with PEP 750 t-strings.

pysx renders reactive user interfaces from Python, in the spirit of Phoenix LiveView. State
lives on the server as signals — one graph per websocket connection — events travel up, and
minimal patch operations travel down. The browser runs about ninety lines of JavaScript.

Templates are **t-strings** ([PEP 750](https://peps.python.org/pep-0750/), Python 3.14),
which give the static/dynamic split a compiled template needs. f-strings cannot work: they
interpolate eagerly and destroy the holes before any library sees them.

---

## Features

- **Signals, derived signals, and effects** with automatic dependency tracking.
- **An indentation-based markup DSL** carried inside a t-string, not built at runtime.
- **Keyed list reconciliation** that leaves untouched rows' DOM nodes alone, preserving
  focus, selection, and scroll.
- **Styled components** with import-time scoped-class hashing.
- **Two-way input binding** with echo suppression, so the caret survives typing.
- **Per-connection isolation** — one client's state never leaks into another's.
- **Editor tooling**: a TextMate injection grammar and advisory diagnostics for VSCode.

---

## Code Sample

```python
from pysx import component, div, html, signal, styled

Page = styled(div, t"""font-family: system-ui; padding: 2rem;""")
Action = styled("button", t"""cursor: pointer;""")


@component
def app():
    count = signal(0)

    return html(t"""
        Page(id="container"):
            "Count: " {count}
            Action(type="button", onClick={(lambda e: count.set(count() + 1))}):
                "Increment"
    """)
```

Two rules the DSL enforces, both consequences of eager interpolation:

- **Only a bare signal in a hole is reactive.** `{count()}` freezes at its first value.
- **Lambdas must be parenthesised.** A bare `lambda` is a `SyntaxError`, because `:` starts
  a format spec.

---

## Requirements

- **Python 3.14 or newer** — t-strings do not exist before it. [uv](https://docs.astral.sh/uv/)
  fetches a suitable interpreter automatically.
- One runtime dependency: `websockets`.

---

## Running an example

```bash
uv run --project . example run counter    # http://127.0.0.1:8750
uv run --project . example run todos
uv run --project . example run            # lists the available examples
```

To choose a port, set `PSX_PORT` or pass `--port`:

```bash
PSX_PORT=9100 uv run --project . example run counter
```

`uv run` needs no activated environment — it prepares one itself. The command is defined in
`run_example.py` at the repository root.

To drop the `uv run --project .` prefix, activate the environment first:

```bash
source .venv/bin/activate     # prompt becomes (pysx)
example run counter
```

`example` is installed into `.venv/bin`, so it is only on `PATH` while that environment is
active. A shell reporting `command not found: example` has not activated it — a new terminal
starts without it.

---

## Tests

Install the locked development tools and the Node/browser/Pylance inputs described in
[verification setup](tests/VERIFICATION.md), then run the full Python suite and quality checks:

```bash
uv sync --locked
uv run --project . pytest -q
uv run --project . ruff check .
uv run --project . mypy
uv run --project . pyright
```

`uv run --project . pytest -q -m 'not acceptance'` excludes the socket acceptance cases;
the harness checks still require the documented Node/browser/Pylance inputs.
Headless acceptance requires permitted loopback access. Its direct entry points remain:

```bash
uv run --project . python tests/acceptance.py
uv run --project . python tests/acceptance_todos.py
```

Run DOM checks in Chromium, Firefox and WebKit, actual Pylance grammar assertions, and
fresh VSCode-host tests with:

```bash
npm --prefix tests run browser
npm --prefix tests run grammar
npm --prefix editor test
```

---

## Editor support

```bash
./scripts/install-extension.sh     # build and install the VSCode extension
./scripts/uninstall-extension.sh
```

This provides syntax highlighting for the markup inside `html(t"""...""")` and diagnostics
for unknown component tags, unparenthesised lambdas, and signals that were called when a bare
signal was meant.

Diagnostics are **advisory by design**. A template with a bad attribute still runs until
first render, the way TypeScript and JSX are advisory rather than build-blocking. Highlighting
applies at literal call sites only: `html(` and `t"""` must sit on the same line, because
TextMate grammars have no dataflow.

Diagnostics refresh when a file is opened or saved, not as you type.

---

## Layout

```
pysx/          the library: reactive core, parser, renderer, server, checker
examples/      runnable examples, one module each, discovered automatically
run_example.py the `example` command
editor/        VSCode extension
docs/          protocol and grammar reference
```

Adding `examples/<name>.py` with an `app` attribute is enough to make
`example run <name>` work — the registry globs the directory.

---

## Documentation

- [docs/PROTOCOL.md](docs/PROTOCOL.md) — the wire protocol: messages, ops, keyed
  reconciliation, and echo suppression.
- [docs/GRAMMAR.md](docs/GRAMMAR.md) — the DSL grammar of record and the hole-kind table.

---

## Relationship to NTML

pysx is a port of [NTML](https://github.com/jmsapps/ntml), a client-side reactive SPA renderer
written in Nim. NTML is today the more capable project, and it is architecturally stronger for
what SPAs do.

The single difference that shapes everything here: Nim macros give NTML a compile-time DSL,
and Python has no macros. Client-side Python would require Pyodide or a Python-to-JavaScript
compiler, both of which defeat the lightweight premise. **So pysx renders on the server** —
and every additional concern in this repository, from the wire protocol to per-session signal
graphs, follows from that one decision.
