# VSCode support

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
