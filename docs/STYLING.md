# Styling

Declare native components with `styled.section(t"padding: 8px;")` and extend a
component with `styled(Panel)(t"color: red;")`. Extension accepts native markers,
styled components, and ordinary callables returning a Template or Fragment.
The two-argument form is removed. `styled.fragment` is unavailable because a fragment
has no element to carry a class. Native factories have tag-specific typed attributes.

```python
Panel = styled.section(t"""
    padding: 16px;
    &:hover:
      border-color: var(--accent);
    @media (max-width: 600px):
      padding: 12px;
""")
TreePanel = styled(Panel)(t"background: var(--surface);")
Action = styled.button(t"min-height: 44px;", variants={
    "primary": t"background: var(--accent); color: white;",
    "ghost": t"background: transparent; color: var(--muted);",
})
view = html(t"""
    {TreePanel}(id="preview"):
      {Action}(variant="primary"): "Save"
""")
```

Nested selector and at-rule headers end in `:` and use spaces for indentation.
`&` anchors the component class; pseudo-classes, modifier classes, attribute selectors,
descendants, `@media` and `@supports` compose to at most 8 levels and 128 blocks per
declaration. Selectors cannot escape their component; sibling selectors targeting
outside it are rejected. CSS braces and interpolations are rejected. Document-level
rules belong in `global_style()`.

Declared variants generate a finite mapping of classes. `variant` accepts
`str | Signal[str] | None`, switches classes without replacing elements, and is
consumed before DOM emission or callable prop forwarding. An unknown name raises
with the declared names. Extension inherits variants; declarations for the same name
append in lineage order. Use CSS variables for continuous values and registered theme
variables for theme-dependent appearance.

Each inheritance chain flattens its declarations into one rule, preserving their order.
Later declarations override earlier declarations independently of registration order.
When several pysx classes land on one element, the stylesheet emits them in the order
they are applied, innermost first, so a wrapping styled callable or a runtime `css`
rule overrides the element's own styled base at equal specificity.
`css_class` exposes the final scoped class as an opaque identifier; inheritance can
change it and applications should not depend on its digest length. Literal and reactive
`class` attributes are
merged with that class, including after updates.

Styled callables retain their Python call signatures and forward props/children through
the [composition contract](GRAMMAR.md#callable-composition). Their class is applied to
every exposed top-level element, including transparent fragment roots, without adding
a DOM wrapper or styling nested descendants directly.

`global_style()` accepts a plain string containing ordinary CSS blocks.
`css(t"padding: 8px")` validates a literal/snapshot flat CSS body and returns a string.
Pass reactive rules to the `css` attribute directly; the helper rejects signals.

## Runtime rules and variables

Use `div(css={rules})` for flat runtime CSS; `rules` can be a string, literal t-string,
or string signal. Rules are deduplicated within that render and released when their
owning branch, row or session disappears. Definitions made during app/component rendering
also belong to that render. Import-time literal definitions can be shared.

`styleVars={({"accent": color, "gap": "8px"})}` (also `cssVars`) accepts a mapping with
literal strings or string signals. A signal containing a string mapping is also supported.
Names are trimmed and normalized to two leading hyphens. Empty values remove the inline
variable; invalid names and compound declaration values raise errors. Ordinary `style`
declarations are preserved and reactive updates read both sources.

## Named themes

Create `themes = Themes()` inside the app, register immutable definitions with
`themes.register("dark", {"ink": "white", "canvas": "black"})`, select a name or Theme
with `themes.select(...)`, and return `html(template, themes=themes)`. The root render
owns this explicit theme state; each websocket app invocation creates its own instance.
`themes.read(name)` retrieves a definition, `themes.current()` reads the active theme,
and `themes.clear()` resets it. `register_vars(...)` can reserve additional variable names.
Missing values in a selected theme and cleared themes emit `unset` for registered names.
The stylesheet snapshot reflects the active theme at render and after every update.

## Editor feedback

Direct literal arguments to `styled()`, `css()` and `global_style()` receive CSS
highlighting, including properties, comments and custom variables. Template regions and
Python interpolation/call scopes remain distinct. TextMate does not follow variables,
aliases or arbitrary CSS factories; those require semantic analysis. The checker reports
constant invalid bases and unsupported CSS interpolation/format metadata using raw source
ranges, including UTF-16 columns and multiline ends. It never executes application code.
