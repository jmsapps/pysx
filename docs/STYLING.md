# Styling

`styled(base, t"color: red; padding: 8px")` accepts an element marker, another styled
object, or an ordinary callable returning a Template or Fragment. CSS is a flat list
of declarations; blocks and interpolations are rejected. Use element objects rather
than strings as bases.

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
