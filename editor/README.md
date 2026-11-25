# PYSX — reactive interfaces in Python

PYSX is a server-side reactive UI library for Python. Build interfaces with
indentation-based markup inside Python t-strings, keep state in signals, and send
targeted updates to the browser when that state changes. Python handles user events
on the server; a small JavaScript client applies the updates.

This extension provides editor support for the
[PYSX framework](https://pypi.org/project/pysx-framework/).

## Install

Use Python 3.14 or newer. Install the framework in your project's
Python environment:

```sh
python -m pip install 'pysx-framework==0.7.2'
```

Or, in a uv project:

```sh
uv add 'pysx-framework==0.7.2'
```

The extension automatically installs and manages its editor tools.
See the [quickstart](https://github.com/jmsapps/pysx/blob/master/docs/QUICKSTART.md)
to run your first application.

## VS Code support

Install **PYSX** (`jmsapps.pysx`) from the VS Code Marketplace. Select a
Python 3.14 or later environment with PYSX installed; the Python
extension's selected interpreter is used unless `pysx.pythonPath` is set.

You can also install and remove it from the command line, outside any checkout:

```sh
pysx --install-vscode-extension
pysx --uninstall-vscode-extension
```

Both commands need VSCode's `code` command on your PATH. If it is missing, open VSCode
and run **Shell Command: Install 'code' command in PATH**, or point `PYSX_VSCODE_CLI` at
the `code` executable. Neither command needs `--app` and neither starts an application.
They report VSCode's own output and exit nonzero when an install or removal fails, so a
Marketplace or network problem is never reported as success. Installing or removing the
extension changes VSCode, not your project: no project settings are touched.

### Tooling and setup

The extension provisions the developer tooling it needs — Ruff and Pyright at exactly
pinned versions — into its own storage, and uses your selected interpreter's PYSX for
analysis. Your application needs no editor-only extras, and its environment, manifests
and lockfile are never modified.

Once analysis is ready, the extension offers to configure the folder so template-only
imports survive organize-imports and unused-symbol checks. The offer explains what it
changes, appears only in trusted workspaces, and can be declined permanently. Run it
yourself at any time with **PYSX: Configure Template-Aware Imports**, and undo it with
**PYSX: Restore Editor Settings**, which only reverses settings PYSX wrote and leaves any
you changed afterwards alone.

To override the provisioned tools, set `pysx.pythonPath` to an environment that already
has compatible versions installed. Pyright additionally needs Node.js; if it is missing,
template diagnostics and highlighting continue to work without it.

For command-line tooling outside VS Code, install `pysx-framework[lsp]==0.7.2`
for the language server, Pyright and Ruff, or `pysx-framework[dev]==0.7.2` for
Ruff alone.

### Where the extension writes

Analysis projections are disposable files under
`.pysx/<project-namespace>/temporary/editor-worker/`, removed when superseded or the
session ends. The state root ignores itself; the existing `_pysx_revision_*` explorer
exclusion also covers older snapshots. Set `PYSX_STATE_DIR` to choose another state root.
Definitions and coordinated rename analyse the frozen snapshot as its own project, so hidden state
directories retain cross-file rename without changing your analysis exclusions.

Managed tooling is separate: it is shared across applications and lives in the
extension's own storage, never in your project.

### Developing the extension itself

These scripts are repository tools and are not part of the Python distribution:

```sh
./scripts/install-extension.sh
./scripts/uninstall-extension.sh
```

Either script changes the installed extension but not the running window, so run
**Developer: Reload Window** afterwards. A local build and the published extension share
the identifier `jmsapps.pysx`, so installing a local build replaces the published one,
and uninstalling removes whichever build is currently installed.

The extension provides syntax highlighting for markup inside `pysx(t"""...""")`
and advisory diagnostics for unknown component tags, unparenthesised lambdas,
and called signals where a bare signal was intended.

Highlighting applies at literal call sites, including calls where a formatter puts
the opening t-string on the next line. TextMate grammars do not follow variables or dataflow.
Diagnostics refresh when a file opens or is saved, rather than as you type.

### Component colours

Component tags are green and never emboldened, for both uppercase and lowercase
bindings. Two surfaces colour them, and both are overridable.

Uppercase tags in direct literals come from the injection grammar. Override the
TextMate scope in your own settings:

```json
"editor.tokenColorCustomizations": {
  "textMateRules": [
    { "scope": "support.class.component.pysx", "settings": { "foreground": "#2EA043" } }
  ]
}
```

Lowercase, binding-aware component names are decorated by the extension and follow
the `pysx.markup.class` theme colour, which has separate dark, light and high
contrast defaults. Override it per theme:

```json
"workbench.colorCustomizations": {
  "[Default Light Modern]": { "pysx.markup.class": "#1A7F37" }
}
```

Setting `fontStyle` on the TextMate scope restores bold if you prefer it.

See the [template grammar](https://github.com/jmsapps/pysx/blob/master/docs/GRAMMAR.md)
for authoring rules and
[verification setup](https://github.com/jmsapps/pysx/blob/master/tests/VERIFICATION.md)
for extension build and host tests.
