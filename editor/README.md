# PYSX — reactive interfaces in Python

PYSX is a server-side reactive UI library for Python. Build interfaces with
indentation-based markup inside Python t-strings, keep state in signals, and send
targeted updates to the browser when that state changes. Python handles user events
on the server; a small JavaScript client applies the updates.

This extension provides editor support for the
[PYSX framework](https://pypi.org/project/pysx-framework/).

## Install

Use Python 3.14 or newer. Install the framework and editor tooling in your project's
Python environment:

```sh
python -m pip install 'pysx-framework[lsp,dev]==0.7.0'
```

Or, in a uv project:

```sh
uv add 'pysx-framework[lsp,dev]==0.7.0'
```

The `lsp` extra supplies the language server; `dev` supplies the pinned Ruff formatter.
See the [quickstart](https://github.com/jmsapps/pysx/blob/master/docs/QUICKSTART.md)
to run your first application.

## VS Code support

Install **PYSX** (`jmsapps.pysx`) from the VS Code Marketplace. Select a
Python 3.14 or later environment with PYSX and Ruff installed; the Python
extension's selected interpreter is used unless `pysx.pythonPath` is set.

To build and install the same extension locally from the repository root:

```sh
./scripts/install-extension.sh
./scripts/uninstall-extension.sh
```

Either script changes the installed extension but not the running window, so run
**Developer: Reload Window** afterwards.

The extension provides syntax highlighting for markup inside `pysx(t"""...""")`
and advisory diagnostics for unknown component tags, unparenthesised lambdas,
and called signals where a bare signal was intended.

Highlighting applies at literal call sites, including calls where a formatter puts
the opening t-string on the next line. TextMate grammars do not follow variables or dataflow.
Diagnostics refresh when a file opens or is saved, rather than as you type.

See the [template grammar](https://github.com/jmsapps/pysx/blob/master/docs/GRAMMAR.md)
for authoring rules and
[verification setup](https://github.com/jmsapps/pysx/blob/master/tests/VERIFICATION.md)
for extension build and host tests.
