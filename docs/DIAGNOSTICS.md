# Static diagnostics and source edits

`uv run --project . python -m pysx.check examples/counter.py` prints JSON (`[]`
for clean source). Findings are advisory: unknown native attributes can still
render. Invalid grammar and runtime values retain their ordinary runtime errors.
Analysis never imports application modules or evaluates interpolations.

For an unsaved buffer, use `diagnostics_for_source(source, filename,
workspace_roots=(root,), buffers={absolute_filename: source})` from `pysx.check`.
The path wrapper is `diagnostics(Path(filename))`. `compiler.analyze` returns a
reusable compilation containing lexical scopes, parsed syntax, maps and workspace
bindings; pass it as `compilation=` when requesting diagnostics for the same buffer.
`analysis.projection_for_source` exposes these diagnostics with a mapped Python
projection and component/prop sites.

Each finding has `code`, `message`, `severity`, zero-based `line`/`startChar`, and
`endChar`; `endLine` defaults to `line`. Columns use UTF-16 code units and ranges
are end-exclusive. Ranges refer to original source, including escapes and braces,
not decoded template text. EOF errors select a nonempty source range.

| Code | Meaning |
| --- | --- |
| `python-syntax` | Incomplete or invalid Python buffer |
| `template-source`, `template-syntax` | Unavailable template source or structural grammar error |
| `unknown-component` | No binding exists in the template's Python scope |
| `ambiguous-marker`, `type-only-component` | Competing marker bindings or a runtime tag imported only for typing |
| `partial-schema-unknown` | A partial's dynamic arguments prevent reliable prop analysis |
| `template-fstring`, `template-lambda` | Eager f-string template or unparenthesized interpolation lambda |
| `template-freeze`, `reactive-coercion` | A snapshot or Python coercion loses reactive behavior |
| `style-declaration`, `interpolation-metadata` | Invalid styling, variant or interpolation metadata |
| `native-attribute`, `native-value` | Known native schema or scalar-value mismatch |
| `event-handler`, `binding-value`, `binding-control` | Proven invalid handler, writable binding or form control |
| `component-required-prop`, `component-unknown-prop`, `component-prop-value` | Proven callable signature mismatch |
| `form-loop-key` | Bound form controls in a DSL loop without an explicit stable key |

Native rules query the same schema as typed constructors, and `native-value`
holds literals to the generated signature's declared type rather than to what the
renderer tolerates. A boolean attribute therefore wants `bool`: `disabled={1}` and
`disabled='bad'` both warn even though both render as `<input disabled>`, because
both contradict the `bool | Signal[bool] | None` the typed constructor declares.

Data/ARIA attributes, custom `onXxx` events and custom elements remain supported.
`svg` and `math` carry no attribute table, so `native-attribute` never rejects a
name inside those foreign namespaces. Callable checks inspect known signatures,
defaults, children and simple builtin literal annotations, including workspace
aliases, partials and styled ancestry. Dynamic factories, competing assignments,
unresolved annotations and arbitrary expressions remain unknown. Conversions and
format specifications are not guessed. The strict
[quality adapters](AUTHORING.md) add full Python type inference to these static
facts; a clean advisory result alone is not a strict type-checking result.

Some findings provide `fixes`, each containing a title, `source_digest` and raw
source edits (`start`, `end`, `text`, `expected`). Edit offsets are Unicode code
points, **not UTF-16 columns**. Apply a selected fix with
`pysx.check.apply_fix(source, fix)`, then analyze the returned source again. This
rejects stale source, invalid ranges and overlapping edits before changing text.

Fixes currently cover a direct frozen Signal call and a single literal f-string
prefix. Ambiguous bindings, concatenated f-strings, nested f-strings and comments
inside a call do not receive guessed edits. Import cleanup uses the separate
[template-aware tooling commands](AUTHORING.md); it must retain genuine template
references and genuine unused-import checking.
