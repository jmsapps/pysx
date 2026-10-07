# pysx DSL grammar of record

The indentation-based markup grammar carried inside a PEP 750 t-string. `parser.py`
implements this; `check.py` validates against it.

Native tags use their HTML spelling, including `div`, `object`, `template` and `var`.
All fourteen HTML void elements emit no closing tag. `fragment` emits its children
without a wrapper and accepts no DOM attributes. HTML attributes normalize camelCase and
Python keyword spelling through the shared schema; `className`/`htmlFor` map to
`class`/`for`, and data/ARIA camel prefixes map to hyphenated names.
Schema diagnostics remain advisory. SVG/MathML entry elements record foreign
namespace inheritance; SVG `foreignObject` children reenter HTML. Foreign attribute
case is preserved, including `viewBox`.

Event holes accept a plain callable or `on_event(callback, ...)`. The latter receives
a typed snapshot and declares synchronous browser policies. `onCustom={on_event(...,
event_type="custom-ready")}` registers a bounded custom event name; arbitrary JavaScript
and custom event detail are outside this interface.

`ref={dom.ref()}` attaches an owned DOM ref marker. Capture a mounted node with
`ref.handle()` and await its methods from an async callback. The marker may remount;
previously captured handles remain tied to their original node. An imperative ref
root must be empty in markup and owns its explicitly created descendants.

```
template   := NEWLINE line*
line       := INDENT (element | conditional | alternative | loop | match | case | local | content)
element    := (NAME | HOLE) [ "(" attrs ")" ] ":" [ content ]
conditional:= "if" HOLE ":"
alternative:= "elif" HOLE ":" | "else" ":"
loop       := "for" (NAME | "(" NAME ("," NAME)* ")") "in" HOLE ["key=" HOLE] ":"
match      := "match" HOLE ":"
case       := "case" ("_" | (STRING | NUMBER | "True" | "False" | "None" | HOLE) ("|" pattern)*) ":"
local      := ("let" | "set") NAME "=" HOLE | "discard" HOLE
content    := item (";" item)*
item       := STRING | HOLE
attrs      := attr ("," attr)* [","]
attr       := NAME "=" (STRING | HOLE)
STRING     := double-quoted or single-quoted text with quoted escapes
HOLE       := a gap between two t-string fragments
```

## Hole kinds

Decided by **position and attribute name**, never by the value's Python type.

| position | name | kind | rendered as |
|---|---|---|---|
| attribute | recognized `on...` event or `on[A-Z]…` | `EVENT` | `data-pysx-{type}="hN"` |
| attribute | `bindValue`, `bindChecked`, `bindSelected` | `BIND` | typed control binding + `data-pysx-binding="hN"` |
| attribute | other | `ATTR` | element gets `data-pysx-el="eN"` |
| `if` header | — | `COND` | `<pysx-slot id="N">branch</pysx-slot>` |
| opening markup line, followed by `(` and/or `:` | — | `TAG` | supplied native/styled/callable component |
| content | — | `TEXT` | `<pysx-slot id="N">value</pysx-slot>` |
| loop source / explicit key | — | `SOURCE` / `KEY` | bounded rows with keyed or positional identity |
| local statement | — | `LOCAL` | lexical declaration, assignment or discarded expression |
| match selector / case pattern | — | `MATCH` / `CASE` | selected owned branch |

A `TEXT` hole holding an `Each` renders as a keyed list. A `Template` or `Fragment` hole
inserts its node tree, so typed native constructors compose inside templates.
`Children` holes insert caller-owned parsed child blocks as described below.
These distinctions are confined to content position; event and binding attribute
names are consumed by the DSL before values are evaluated.

List/tuple content renders recursively as a snapshot, including raw Templates and
Fragments within it. Each entry retains its namespace, styles and captured handlers;
ordinary values are escaped. No snapshot watchers are created, even for a Signal
inside an entry: its value is read once. Direct Template/Fragment composition keeps
the existing explicit live holes. Snapshot traversal is bounded to 10000 entries
and 128 levels, including nested containers. Comprehensions and bounded Python loop
builders produce ordinary snapshots; changing their source cannot rebuild them.

Raw Signal dispatch comes first. A Signal holding a list, tuple, Template or Fragment
uses one owned content watcher and coarse HTML updates; unchanged markup emits no
operation. Its rendered entries remain escaped and retain their namespaces. Use a
DSL loop or `each()` for keyed row reconciliation. The checker warns conservatively
about known Signal-dependent sequence/comprehension expressions and assigned aliases;
it does not evaluate code or inspect deferred lambda bodies.

A hole opening a markup line becomes a component tag only when followed by an
attribute list and/or `:`. `{Card}(id="preview"):` and `{shared.Card}:` use real
Python references, including local bindings, imported aliases and partial callables.
`{native.Input}()` is a childless tag. In all other positions holes retain their
content meaning. Unsupported tag values raise a TypeError naming the received type.
Parse caches contain hole indexes and syntax only, never component or session values.
Literal-name tags and explicit `namespace` dictionaries remain available.

## Branches and lexical loops

`elif` and `else` must immediately follow the preceding branch at the same parent
and indentation. `match` contains `case` blocks; alternatives use `|`, and the
wildcard `_` must be last. Inactive branches release subscriptions and owned state.
Conditional attributes use ordinary Python expressions: pass a `derived` Signal
for a live choice, or a deferred expression inside a row. An attribute value of
`None` omits the attribute.

Python evaluates t-string holes before markup is parsed. Declare `row =
Binding[Row]("row")`, supply `namespace={"row": row}`, and write `for row in
{rows} key={defer(row, lambda value: value.id)}:`. Content and attributes can use
`{row}` directly, or `defer(row, callback)` and `defer2(first, second, callback)`
for typed expressions. Captured handlers retain their row's immutable environment.
No text is evaluated and no execution frame is retained. Components keep bare tags
with `use=`; `namespace` here supplies lexical identities.

Tuple patterns destructure source entries; use `enumerate` for indexed snapshots
or a derived enumerated source for live indices. Nested loops may shadow bindings.
`let name = {value}` introduces a value in the current body; `set name = {value}`
requires an existing binding and shadows it in that body. `discard {expression}`
evaluates a deferred expression without emitting markup. These expressions run
again when their enclosing reactive row renders, so side effects must tolerate
repeated evaluation. Child bodies cannot mutate the parent's environment.

Loops traverse at most 10000 entries. Explicit keys must be unique non-None
strings or integers; the renderer uses their string representation as identity.
Omitting `key=` uses position: state survives replacement at that position,
and removing then reinserting a position remounts it. A keyed row retains its
component state across reordering. Snapshot sources create no loop watchers;
Signals and deferred live sources update through the existing list owner.
`bounded_while(condition, builder, limit=10000)` is the ordinary Python while
equivalent: it returns a tuple of snapshot results and rejects nontermination
past the requested limit. Comprehensions remain ordinary Python snapshots.

## Template coordinates and interpolation metadata

Attribute lists may continue across lines. Python holes remain atomic, including
nested expression delimiters. Markup strings accept either quote and `\\`, `\"`,
`\'`, `\n`, `\r`, and `\t` escapes; unknown escapes retain their backslash. Python
decodes ordinary t-string escapes first, so use raw t-strings or double backslashes
when a quoted escape must reach the markup scanner. CRLF is accepted. Structural
indentation uses spaces; tabs in indentation raise a positioned syntax error.
Literal braces use Python's doubled-brace spelling.

Assigned and assembled Templates retain their fragments and interpolation metadata.
Adjacent holes retain the empty static fragment between them. Syntax nodes are immutable,
with spans in original decoded fragment indexes/offsets and zero-based logical lines/
columns; a hole occupies one logical column. These coordinates are distinct from raw
Python source offsets and editor UTF-16 columns. Text, tag, attribute values and complete
element/conditional blocks carry coordinates. The structure-only parse cache holds 256
entries; each template is bounded to 1 MiB of UTF-8 static text, 16384 holes and 128 nested
blocks. It never stores interpolation values.

Text and ordinary attributes honor conversion (`!s`, `!r`, `!a`) followed by Python's
format specification. Snapshot values format once; a formatted Signal stays live and
updates its text or attribute watcher. Explicit formatting turns structural snapshot
values into escaped text. Metadata is rejected on event, binding, condition and component
tag holes, and on CSS capability attributes (`css`, `styleVars`, `cssVars`), with the
offending hole's template position. Formatting errors propagate rather than being ignored.

`else:` binds to the most recent `if` opened at the same indent.

Children come from an indented block under an element, from inline `content` after the `:`,
or both.

## Callable composition

Names bound to ordinary callables are component tags, including uppercase and lowercase
aliases; reserved native tag names retain their native meaning. The compatibility
`@component` decorator is optional. Attribute values are forwarded as keyword props,
preserving live objects. Nonempty child blocks are forwarded as `children`,
a `Children` object retaining the caller's parsed nodes, hole values and namespace.
For callable bases declaring `*children`, that object is passed positionally, matching
typed native constructors. Otherwise it is forwarded as the `children` keyword.
Insert that object in a content hole to render it in its caller environment.
Components return a `Template` or `Fragment`; other return types raise `TypeError`.

Defining-module bindings and actual Python closure cells provide a component's namespace,
so an imported or module-level component resolves from its bare name with no extra argument.
`html(template, use=(Card, Panel))` names the components a template uses: each entry is an
ordinary Python reference, and an entry carrying a `__name__` also binds under it, which
covers components defined inside the calling function. `html(template, namespace={...})`
remains available for a binding whose markup name differs from the object's own, and
`render(app, namespace={...})` provides explicit root bindings.
These mappings are copied; execution frames are never retained. Partials and callable
instances use their underlying defining callable's module. A fragment's explicit bindings
override module bindings. Caller children retain their own namespace when inserted.

Name imports used only as literal tags in `use=`. For example, `from shared import Card`
together with `html(t'\nCard:', use=(Card,))` provides a real Python reference that Ruff and
Pyright recognize; without one, both report the import as unused and Ruff's fix removes it. Their unused-import
checks and import cleanup continue to apply to unrelated imports. Required, default and
keyword-only props follow the callable's Python signature; missing or unexpected props
raise `TypeError`. Python annotations are checked on ordinary component calls by static
type checkers, rather than enforced as runtime coercions. Signals are passed intact;
reading a Signal before passing it produces a snapshot. Strings inserted as content,
including string-valued component props, are HTML-escaped.

### Component ownership

Each render owns a root scope. Callable tags and keyed `each` item factories have child
scopes. Direct Python function calls share the active scope. Use a callable tag, keyed
`each` factory or root `render` call when the function should own a separate scope.
`local_state("name", initial)` returns the same Signal for a surviving scope; the initial
value is copied once. Use a stable name and payload type for each state entry.
Keyed reorder preserves scopes. Removing a key, changing a conditional branch or changing
the component callable identity disposes its subtree; a subsequent remount starts fresh.
Identity is the callable's definition — a function or method body, or a callable
instance's class — so rebuilding that callable for each render keeps its scope.
Local state rejects writes after disposal. Component functions may run again to produce
markup, so put one-time external setup in `on_setup(callback)`. Setup writes reach holes
rendered before the owner through the session's first patch, not through its initial HTML.

`on_cleanup(callback)` registers teardown during first setup or an `on_mount` callback.
It runs once, in reverse registration order; descendants close before parents. Cleanup
continues after exceptions and reports an `ExceptionGroup` after releasing resources.
Failed setup cleans the failed subtree; failed initial rendering cleans the render.
Sessions dispose their render on disconnect; standalone callers use `Rendered.dispose()`.
`on_mount(callback)` runs once after the browser acknowledges the owner's initial DOM
commit, separately from server setup. Repeated or late acknowledgements do nothing.
These hooks accept synchronous callbacks; use managed tasks for asynchronous work.

`own_effect(name, callback)` reuses an owned reactive effect. `own_subscription(name,
subscribe)` calls `subscribe()` once and owns its returned unsubscribe callback.
`own_timer(name, delay, callback)` owns a one-shot asyncio timer; `own_task(name, factory)`
owns an asyncio task produced once by the coroutine factory. Timer/task registration needs
a running event loop. Teardown cancels both, and timers guard disposed owners. Tasks must
cooperate with cancellation; local state also guards late writes. Resource names stay
stable across renders; each scope bounds state, resources and hook registrations to 1024
entries per category. Timer/task ownership does not add a server-push transport.

Root exposure is transparent: a single element exposes that element; multiple elements
expose every top-level element; `fragment:` and fragment content holes expose their
contained roots recursively. Text roots remain text. No wrapper element is introduced.

## Deliberate bounds

Templates require t-strings: they retain the static markup and interpolated values
separately. f-strings interpolate eagerly and cannot preserve reactive holes.
A bare signal such as `{count}` stays reactive; `{count()}` reads a value once
and freezes that hole. Parenthesise lambdas in holes: without parentheses, the
colon is interpreted as the start of a t-string format specification.

- **Attribute lists are single-line.** An unclosed `(` at end of line is an error.
- **No escape sequences in text literals.**
- **Event names come from the shared schema**, including body-specific events;
  capitalized `on[A-Z]…` names retain the open event convention. Event attributes
  require interpolated callables. Holes cannot appear in attribute-name position.
- **Templates must begin with a newline.**
