# pysx DSL grammar of record

The indentation-based markup grammar carried inside a PEP 750 t-string. `parser.py`
implements this; `check.py` validates against it.

Native aliases `d`, `obj`, `tmpl` and `v` resolve to canonical tags. All fourteen
HTML void elements emit no closing tag. `fragment` emits its children without a
wrapper and accepts no DOM attributes. HTML attributes normalize camelCase and
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
line       := INDENT (element | conditional | alternative | content)
element    := NAME [ "(" attrs ")" ] ":" [ content ]
conditional:= "if" HOLE ":"
alternative:= "else" ":"
content    := item (";" item)*
item       := STRING | HOLE
attrs      := attr ("," attr)* [","]
attr       := NAME "=" (STRING | HOLE)
STRING     := '"' [^"\n]* '"'          ; no escapes; first '"' closes
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
| content | — | `TEXT` | `<pysx-slot id="N">value</pysx-slot>` |

A `TEXT` hole holding an `Each` renders as a keyed list. A `Fragment` hole
inserts its node tree, so typed native constructors compose inside templates.
These distinctions are confined to content position; event and binding attribute
names are consumed by the DSL before values are evaluated.

`else:` binds to the most recent `if` opened at the same indent.

Children come from an indented block under an element, from inline `content` after the `:`,
or both.

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
