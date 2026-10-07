# Native elements

Lowercase markers in `pysx.elements` are frozen `ElementTag` objects. Their string
form is the canonical HTML name, and they work with `styled(Base)(css)`.
`styled.<tag>(css)` declares a typed styled native directly, without a marker import;
write `Card:` in the template and include `use=(Card,)` in `html()` so Python
import tools see the component reference.
Every marker is named for its HTML tag; Python uses `del_` for the delete
element, whose HTML name is a keyword.

`pysx.native` provides typed constructors such as `Div`, `Input`, `A` and
`Select`. They return composable fragments, accept positional children and
provide tag-specific attribute completions. For example:

```python
from pysx.native import A, Div

view = Div(A("Home", href="/"), class_name="navigation")
```

Attribute keywords use HTML spelling, with underscores for hyphens and trailing
underscores for Python keywords. `class_name` and `html_for` map to `class` and
`for`. Native booleans accept bools; text fields accept strings; numeric fields
accept numbers or HTML numeric strings. Signals of these types are also accepted.
Event keywords such as `on_click` accept callables separately from attributes.

`custom_attrs={"aria-hidden": False, "data-role": "menu"}` deliberately escapes
native attribute typing. `custom_element("my-widget", custom_attrs={...})`
constructs an open custom element. Attribute names are validated; inline event
strings and reserved runtime markers cannot use this escape.

`pysx.schema` exposes immutable baseline rows, native tag names, families,
events, `tag_info()`, `allowed_attr()` and `normalize_attr()`.
Template schema validation is advisory; typed Python constructors let static
checkers reject wrong native combinations. `fragment` emits children without a
DOM wrapper and accepts no DOM attributes. Void elements reject constructor
children. SVG and MathML entry metadata declare foreign namespaces; descendant
typing is open through custom attributes rather than a full foreign tag schema.

Native signatures are generated from the shared schema:
`uv run --project . python -m scripts.generate_native`.
Use `--check` to detect stale signatures.

Typed controls accept `bind_value=Signal[str]` for text inputs, textarea, single
selects and shared radio groups; `bind_checked=Signal[bool]` for checkbox/radio
checked state; and `bind_selected=Signal[list[str]]` for multiple selects. DSL
spellings are `bindValue`, `bindChecked` and `bindSelected`. Writable projections
work identically. Derived signals and incompatible control/payload combinations
raise TypeError. Radio value bindings require a value attribute on each radio.
Text controls always send strings, including numeric inputs; parse numbers in the
application. Native reset restores initial defaults and synchronizes the bound
signals. Submit handlers receive successful controls, validity and submitter data;
see [the protocol](PROTOCOL.md) for payloads and correction/IME behavior.
