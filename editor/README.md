# VSCode support

From the repository root:

```sh
./scripts/install-extension.sh
./scripts/uninstall-extension.sh
```

The extension provides syntax highlighting for markup inside `html(t"""...""")`
and advisory diagnostics for unknown component tags, unparenthesised lambdas,
and called signals where a bare signal was intended.

Highlighting applies at literal call sites, including calls where a formatter puts
the opening t-string on the next line. TextMate grammars do not follow variables or dataflow.
Diagnostics refresh when a file opens or is saved, rather than as you type.

See the [template grammar](../docs/GRAMMAR.md) for authoring rules and
[verification setup](../tests/VERIFICATION.md) for extension build and host tests.
