# Python template compilation

Applications stay in ordinary `.py` files. Import components normally and name them
in markup; the compiler records real lexical references before Python compiles the
module. The defining binding is captured when the fragment is constructed.

`pysx(template, *, use=(), namespace=None, themes=None)` constructs a `Fragment`.
It replaces the former `html()` helper; update imports and calls to `pysx()`.
There is no compatibility alias. `render(component_fn, *, namespace=None)` remains
the root rendering operation.

```python
from pysx import pysx
from .components import Panel

def app():
    return pysx(t'''Panel(title="Hello"): "Content"''')
```

The server installs the scoped loader before importing the module specified by
`--app package.view:app`. The bundled example launcher and repository tests install
their application scope before collection/imports. For another launcher, install
the loader before any application imports:

```python
from pysx.loader import install_loader

install_loader(packages=("myapp",))
from myapp.view import app
```

An explicit `roots=(Path(...),)` also covers source modules under those roots.
Include every application package that produces fragments, including shared component
dependencies in other packages, before importing them. A wrapper entry point's package
does not automatically cover its dependencies. The bundled example runner installs its
scope before loading example modules.
Framework, hidden and vendor directories are excluded. Installing after an affected
application module has already executed reports an error; restart with the hook
installed first. Ordinary imports retain their module names and source filenames.
Reloading recompiles changed source or static import dependencies; the cache contains
bounded immutable code, never application state or fragments.

Fragments preserve their producer's bindings through content insertion, row builders,
component returns and styled wrappers. Caller-supplied children retain the caller's
environment. Later assignment to a component variable does not retarget an existing
fragment. An absent component in an inactive markup branch fails if that branch
renders. Native HTML spellings remain native; compiler binding does not require a
global component registry or frame inspection.

Existing explicit `use`/`namespace` calls retain their compatibility behavior. Raw
Templates and files imported without the loader use the existing runtime path; bare
tag closure capture requires compilation. Statically unsupported template assembly
is advisory rather than silently inventing bindings.

## Inline row and branch helpers

Use ordinary Python callbacks inside interpolations:

```python
from pysx import each, each_indexed, pysx, when

rows = each(tasks, lambda task: pysx(t'li: {task.title}'), key=lambda task: task.id)
numbered = each_indexed(
    tasks, lambda index, task: pysx(t'li: {index + 1}. {task.title}'),
    key=lambda task: task.id,
)
panel = when(
    conditions=[
        (is_full, lambda: pysx(t'p: "Full"')),
        (is_compact, lambda: pysx(t'p: "Compact"')),
    ],
    default=lambda: pysx(t'p: "Default"'),
)
view = pysx(t'div: {rows} {numbered} {panel}')
```

`each` and `each_indexed` accept a readable iterable, such as a signal, or an ordinary
iterable. Readable inputs update reactively. Ordinary iterable inputs freeze their items
at helper construction and render once without subscriptions. Inputs are bounded to
10,000 rows, including generators. Keys must be strings or integers, excluding booleans,
and unique after conversion to their wire string: `1` and `"1"` collide. Keys identify
items; positions never identify indexed rows. Surviving keys retain local state while
builders rerun with current values and event captures.

Nested readable child sources update through their live parent row. Plain child lists
remain snapshots. Updates retain the current whole-row patch behavior.

`when` accepts only keyword arguments. Conditions are booleans or zero-argument readable
booleans. It explicitly reads conditions in order and constructs only the first matching
builder, or the default when none match. Omitting the default renders nothing. Inactive
builders never run. Selected branches retain local state; leaving a branch disposes its
resources, and returning creates fresh state. Conditions are bounded to 10,000 pairs.
Python expressions used to create the conditions list are evaluated normally; pass a
signal or `lambda: expression` to defer a reactive condition read. These helpers introduce
no deferred-expression API. Markup `if` and `match` still receive eagerly evaluated holes.

## Compatibility and deprecation

`pysx(..., use=...)`, explicit template `namespace=...`, `Binding`, `defer`, `defer2`,
and markup `for`/`let`/`set`/`discard` are deprecated for new authoring. Their existing
behavior, grammar highlighting and exports remain available throughout the current 0.x
compatibility period. This is a documented deprecation; runtime and checker warnings are
not added automatically. Removal requires a separately announced breaking release and
migration window, after the replacement tooling and examples qualify. No removal happens
as part of this migration.

Use the compiler for component names, Python callbacks and assignments for row expressions,
and `when` for lazy branches. Optional markup `if`/`elif`/`else` and `match`/`case`, normal
interpolations and dynamic tag holes remain supported. Dynamic tag holes are useful when
the component itself is selected as a Python value; imported components can use bare tags.

## Production output

Build a source package into a fresh directory:

```sh
pysx-build myapp deployment/myapp
```

For a directory containing multiple packages, pass that directory as the source
and preserve its layout on the output import path. The build copies resources and
emits standard sourceless `.pyc` modules using the same lowering pass as the loader.
Install pysx in the deployment interpreter and import the compiled application
normally; the original application source and development loader are unnecessary.
The output includes compiler/interpreter metadata and source hashes.

Artifacts require the matching Python bytecode magic; rebuild when changing the
interpreter minor version. Original interpolation expressions, conversions, format
specifications and eager evaluation order are preserved. Portable source filenames
and bounded embedded source text support ordinary Python tracebacks. Builds do not
obfuscate source: embedded traceback text is part of the artifact.

## Import checking and Python typing

Run the template-aware tools from the project root:

```sh
uv run --project . python -m pysx.quality ruff
uv run --project . python -m pysx.quality ruff --fix-imports
uv run --project . python -m pysx.quality mypy
uv run --project . python -m pysx.quality pyright
```

Formatting and style rules inspect original Python. Scope and import rules inspect
the compiler projection, so a component used only as a tag counts as a real use.
Safe import cleanup maps only changes to original import declarations. Incomplete
or unsupported source blocks deletion. Strict mypy and Pyright inspect every
maintained Python file in an isolated package-preserving copy; the commands reject
empty or skipped analysis and report original locations. Application modules are
never imported by analysis. Keep the project’s normal strict analyzer settings.

## VS Code authoring

Install the pysx extension, Microsoft Python and Pylance. Select Python 3.14+ with
pysx, Ruff and Pyright installed. The extension uses the selected environment’s executable;
`pysx.pythonPath` is a resource-specific override. It does not invoke `uv` from the
editor or depend on the checkout used to build the extension.

Run **pysx: Configure Template-Aware Imports** after opening a Python file. It first
requires successful workspace analysis and complete analysis of the active file. It
reports progress and failures in the **pysx** Output channel, and displays a
confirmation when setup succeeds. If analysis is interrupted, wait for it to finish
and run the command again. Unsupported active files must be corrected first.
The command configures this workspace
folder to use the pysx unused-import diagnostics and save action. It preserves the
existing type-checking level and other diagnostic overrides, disables Pylance unused
hints and overlapping import/unused-local/function checks, and excludes overlapping Ruff
rules when that extension is installed. Ordinary Python typing remains with
Pylance. Use strict mode for full prop/type feedback.

When `pyrightconfig.json` or `[tool.pyright]` exists, setup updates that effective
project configuration because it takes precedence over editor severity settings.
It preserves other settings, retains strict typing, and excludes private revision
diagnostics. Save pending configuration edits before setup. Unused imports and locals
remain checked by template-aware Ruff; unused functions remain checked by the owned
Pyright projection, including functions referenced only through bare markup tags.

Ruff's unset `lint.ignore` value is nullable; setup treats it as an empty list and
preserves existing ignored rules. The editor qualification host loads Python,
Pylance and Ruff to exercise this configuration together.

Strict Pyright owns generated validator diagnostics; Pylance ignores only the
private revision diagnostic paths and retains IntelliSense there. This prevents
private cache errors from appearing in Problems without relying on its view filter.
The diagnostic-path setting is installed after the owned backend succeeds.
The original project configuration, selected interpreter and coherent snapshot are
used for these checks. Ordinary Python errors remain on their original documents.

The supported cleanup routes are **pysx: remove unused imports** and
`source.organizeImports.pysx` on explicit save. Generic Pylance/Ruff remove-unused
and fix-all save actions are disabled because they inspect the original t-string
as text. Their manual commands are not template-aware; use the pysx action for
deletion. Formatting and import sorting can still run separately. Keep the pysx
extension enabled in workspaces configured this way; restore the prior settings
if removing it.

Hover, tag/prop completion, definitions and references use typed Python projections
of current unsaved buffers. **F2** and **pysx: Rename Python and Markup Symbol**
coordinate Python declarations/imports and their matching markup tags across files.
Generic rename commands invoked by other extensions bypass this route. Quoted
markup text is preserved. Edits are rejected if any participating source changes,
mapping is ambiguous, or analysis is incomplete.

Snapshots use immutable private revision packages under the workspace import root,
including `src/` package layouts. The extension hides these directories and removes
them when replaced or deactivated. Navigation and edits return original source
locations. Late results from obsolete revisions are discarded. Analysis is bounded
to 512 Python sources and 8 MiB per workspace folder; ambiguous source-root layouts
or larger workspaces fail with an explanation in the pysx output channel.

Unchanged projections are reused only when source, static dependencies, configuration,
interpreter and library fingerprints match. New revisions keep their own generated
namespace and source maps. Configuration changes invalidate pending work. Mirrors open
when a language feature needs them; reference search and rename open the complete graph.
Strict checking still covers every projected Python source. Large workspaces can therefore
take longer to finish diagnostics than to build their projection model; readiness timing
includes both steps.
