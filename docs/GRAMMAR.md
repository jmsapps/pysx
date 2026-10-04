# pysx DSL grammar of record

The indentation-based markup grammar carried inside a PEP 750 t-string. `parser.py`
implements this; `check.py` validates against it.

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
| attribute | `on[A-Z]…` | `EVENT` | `data-pysx-{type}="hN"` |
| attribute | `bindValue` | `BIND` | `value="…"` + `data-pysx-input="hN"` |
| attribute | other | `ATTR` | element gets `data-pysx-el="eN"` |
| `if` header | — | `COND` | `<pysx-slot id="N">branch</pysx-slot>` |
| content | — | `TEXT` | `<pysx-slot id="N">value</pysx-slot>` |

A `TEXT` hole holding an `Each` renders as a keyed list. That is a value distinction, not a
structural one — the markup is identical either way — unlike `onClick=`, where the DSL owns
the token and must consume it.

`else:` binds to the most recent `if` opened at the same indent.

Children come from an indented block under an element, from inline `content` after the `:`,
or both.

## Deliberate bounds

- **Attribute lists are single-line.** An unclosed `(` at end of line is an error.
- **No escape sequences in text literals.**
- **`EVENT` iff the attribute name matches `on[A-Z]`**; any other hole in attribute name
  position raises `ATTR_VALUE not supported`.
- **Templates must begin with a newline.**
