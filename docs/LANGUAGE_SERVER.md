# Resident language server

Install `pysx[lsp]` in the selected Python 3.14+ environment, then launch that
environment's Python with `-m pysx.tooling.lsp.lsp`. The optional extra includes the pinned
language framework, Pyright and Ruff. Node.js must be available to the server.
The base renderer and `python -m pysx.tooling.check` do not require the extra.

The server uses stdio JSON-RPC. Stdout contains protocol messages only; logging
goes to stderr. Clients should send explicit workspace folders during initialization. The VSCode
extension launches this server when `pysx.useLanguageServer` is enabled, one
server per workspace folder; the worker integration remains the default.

Open, incremental change, save and close notifications maintain unsaved source
overlays. Position encoding is negotiated with the client. Compiler and type-service
ranges map back to the original buffer, including Unicode and escaped template
text. Analysis reads source and imports statically; it never executes application
modules. Completion offers native tag-specific attributes, valid control bindings
and callable/styled props with exact source replacements. Signature help uses the
maintained type service, including typed callable parameters and incomplete
templates. Hover, definitions and references map component sites to original
declarations and uses, including repeated styled tags and unsaved imported files.
Unknown dynamic bindings may have no static result. Python expressions inside
templates, ordinary Python positions and CSS remain owned by their language tooling.

One resident type process serves each workspace. Projection paths remain stable
across edits; changed modules and affected dependents synchronize without rotating
the package namespace. SDK imports retain the real library identity; user-module
imports use the private projection namespace. Structural features read the current buffer immediately,
without waiting for compiler projections or the type service. Typed current-buffer
requests prioritize that file and its local import closure; remaining projections
compile in cooperative chunks through the same cache and backend. A request queries
the service after its source synchronization without waiting for unrelated workspace
diagnostics. Rename and destructive import actions still require full workspace
coherence. A superseded or not-yet-ready request returns a retryable content-modified
response; analysis startup failure reports an actionable unavailable-analysis error
rather than leaking an internal timeout. Known Pyright progress notifications are
consumed without warning spam.
Dependency generations reject superseded results even when an unchanged consumer
retains its document version. Client cancellations do not cancel backend work;
late backend responses are discarded through generation checks.

Send watched-file notifications for saved sources, dependency changes and project
configuration changes. The effective project configuration is preserved in an owned
projection configuration with strict template validation; the user's files are not
modified. Effective type-configuration changes start a fresh backend epoch so old
configuration diagnostics cannot enter the new snapshot. An initialization option or configuration notification
with `pythonPath` selects the interpreter the type service is pinned to; a later
change starts a fresh type service.
A crashed backend is replayed automatically, with
at most two recovery attempts; exhaustion reports degraded analysis and requires
restarting the language server.

Analysis is bounded to eight workspaces, 512 Python files and 8 MiB of source per
workspace. Closing a document releases its unsaved overlay; closing the last one
also stops the backend and deletes its temporary projection files. Shutdown and
disconnection release owned analysis processes and temporary files.

## Structural features and safe edits

Semantic tokens, folding ranges, document symbols and selection ranges describe
markup only. Tokens are emitted for element tags, attribute names, quoted
attribute values, control keywords and loop or local binding names, every one of
them inside a template literal; Python regions, interpolations, plain text and
CSS are left to their own tooling so no region is owned twice. Folding, symbols
and selection ranges follow the parsed structure, and a selection widens from the
innermost construct out through the template to the whole call. A template the
parser cannot read yields no structural results rather than guesses.
Reusing one template through multiple calls or aliases emits each original token
once; distinct literals with identical text retain their separate source ranges.

The VSCode client requests `markupHighlighting: "decorations"` at initialization.
In this mode the server omits its full-document semantic-token capability so
Pylance retains Python and interpolation expression coloring. Direct literal
calls keep the injection grammar's existing TextMate scopes, with green/bold
component decorations for statically resolved lowercase callable and styled tags.
Native reserved names retain their native highlighting; unresolved, ambiguous and
non-callable lowercase names receive no component override. The client requests
`pysx/markupTokens` for these overrides and for statically resolved aliases and
shared templates beyond literal injection, decorating only markup ranges. CSS values and
Python expressions are excluded. Responses carry document versions and analysis
generations; edits and dependency invalidations clear superseded decorations.
The `pysx.markup.type`, `.class`, `.property`, `.keyword`, `.variable`, and `.string`
theme colors default to the matching symbol colors and can be customized through
`workbench.colorCustomizations`. Standalone clients retain normal semantic tokens
unless they explicitly request decoration mode.

Formatting normalizes structural indentation to four spaces per nesting level and
changes nothing else. Text, quoted values, escapes, multiline attribute
continuations and interpolations are preserved byte for byte, the formatted
template parses to the same structure and hole table as the original, and
formatting the result again is a no-op. Range formatting applies only the
normalizations that fall entirely inside the requested range. The same
normalization is offered as a `source.fixAll.pysx` code action, which is absent
when a document is already normalized.

Rename covers a component's declaration, its imports and aliases, and its matching
template uses in one workspace edit. Import repair follows the resolved declaration
and lexical scope, leaving unrelated same-spelled imports unchanged. When renaming
the canonical declaration, an import such as `Panel as Fancy` changes its imported
name while retaining `Fancy` and its local uses. Initiating rename from that genuine
alias is refused; use the canonical component name instead. Prepare-rename accepts only a component tag
with a visible declaration; native HTML tags, attributes, text, interpolations and
bindings with no static declaration are declined. Rename is refused outright, with
a reason and without partial edits, when the new name is not a Python name, when
the owning declaration cannot be located, when a reference lies outside the
analyzed workspace, or when any reference no longer reads the original name.
Shadowed bindings of the same spelling are left alone.

## Editor ownership

PYSX can take ownership of unused-symbol reporting so that template-only imports,
locals and nested functions stop being reported as dead while genuinely unused
code is still flagged. Ownership is computed, not assumed: `python -m
pysx.tooling.editor.config plan <root>` reads the observed settings, the readiness of
analysis and which replacement checks are available, and returns an ordered list
of steps, a matching rollback list, any project configuration edit and a retained
state record. Setup is refused with a reason when analysis is not ready or when a
check PYSX would silence is not yet replaced.

Applying the steps preserves unrelated user values, and running the plan again on
a configured workspace produces no steps. If an apply is interrupted, replaying
the rollback list restores the original values. `python -m pysx.tooling.editor.config
restore <root>` gives ownership back, and it skips any setting whose current value
no longer matches what PYSX wrote, so a later user edit is reported rather than
overwritten.

## Custom requests

Three requests exist outside the standard protocol, all answered per workspace.
`pysx/status` reports the server process, the selected interpreter and, for each
analyzed root, its generation, backend epoch, backend process, open and cached
document counts, readiness and last error. `pysx/readiness` takes a document URI
and reports whether that workspace's analysis is coherent together with the
replacement coverage ownership setup requires: whether the pinned Ruff resolves,
whether the type backend is running, and whether the owned organize-imports
action is available. `pysx/refresh` takes a document URI, invalidates that
workspace and returns its new generation once analysis is current.

## Using it from the editor

The extension ships both clients and defaults to the existing worker integration.
Set `pysx.useLanguageServer` to `true` to use the resident server instead; the
window must be reloaded for the change to take effect. Each workspace folder gets its own client and its own server,
and a document is always served by its nearest owning folder, so nested roots
never produce duplicate providers or duplicate results; file-backed documents
outside any folder are served by a separate client. The client launches the
selected environment's interpreter with `-m pysx.tooling.lsp.lsp`, resolving it from the
folder's `pysx.pythonPath` when set and otherwise from the Python extension's
environment selected for that folder. Changing either — through settings, the
Python extension, or adding and removing folders — restarts only the affected
clients and resolves the executable afresh, including for a manual restart.
Rapid changes are coalesced and the latest selection always wins. A folder whose
interpreter or `lsp` extra is missing reports the reason and stays recoverable
through a later selection or restart; the other folders keep working. If that environment lacks the `lsp` extra, the client reports it and
stops rather than failing silently — `python -m pysx.tooling.check` still works. A language
server that crashes is recovered at most four times, after which the output channel
explains how to reproduce the failure; `pysx: Restart Language Server` clears that
budget. Intentional restart and shutdown are serialized and do not trigger crash
recovery. Deactivation awaits owned cleanup. The type backend receives its owner's
process ID and exits if that server dies abruptly.

### Commands

`PYSX: Rename Python and Markup Symbol` performs the guarded rename described
above. `PYSX: Refresh Analysis` rebuilds the owning workspace. `PYSX: Configure
Template-Aware Imports` and `PYSX: Restore Editor Settings PYSX Changed` apply and
reverse ownership setup for the active document's folder. `PYSX: Restart Language
Server` replaces that folder's server. Every command is registered once and runs
against the active document's owning client; a command implemented only by the
other integration says so rather than failing as unknown.

Developer tooling is resolved from the selected environment only. Install
`pysx[dev]` there for the pinned Ruff; `python -m pysx.tooling.tools` reports the
resolved path and version, and both the editor and the CLI use that same
executable. A Ruff found elsewhere on `PATH` is never substituted, and a version
other than the pinned one is rejected with an explanation rather than used.


## Setup

1. Install `pysx[lsp]` and `pysx[dev]` into the Python 3.14+ environment you select in
   the editor. The first supplies the language server and its type backend, the second
   the pinned Ruff. Node.js must be available to that environment.
2. Install the extension, then set `pysx.useLanguageServer` to `true` and reload the
   window. Without the setting the extension keeps using its worker integration.
3. Run `python -m pysx.tooling.tools` in that environment to confirm which Ruff is resolved.

Unused-symbol reporting is handed to PYSX by `PYSX: Configure Template-Aware Imports`.
Setup is refused, with the reason shown, if analysis is not ready or if a check PYSX
would silence has no replacement yet. It preserves unrelated settings, does nothing on a
workspace already configured, rolls back an interrupted apply, and refuses to restore any
setting edited after PYSX wrote it. Setup enables the `source.organizeImports.pysx`
action as the replacement for the editor actions it disables. That action removes only
imports the template-aware analysis proves unused, is offered only against complete,
current analysis of the unmodified buffer, and is withheld rather than guessed whenever
analysis has moved on. Save actions never wait for the workspace sweep: if current,
complete analysis is unavailable, that save leaves imports unchanged. Once analysis
finishes, a subsequent action can remove proven unused imports. This avoids delaying
the disk write and hot reload behind language analysis. Repeated setup keeps the original restoration baseline, so
`PYSX: Restore Editor Settings PYSX Changed` still returns the values the workspace had
before PYSX ever configured it.

## Troubleshooting

**No PYSX diagnostics at all.** Check the `PYSX language server` output channel. A
missing `lsp` extra, an unselected interpreter or a Python older than 3.14 are each
reported there explicitly rather than failing silently.

**Diagnostics stop updating.** The server makes at most two recovery attempts for a
crashed type backend; the editor separately permits four language-server crash
recoveries. Exhaustion is reported in the output channel. `PYSX: Restart Language
Server` starts a fresh server and clears its crash budget. `python -m pysx.tooling.lsp.lsp` run
in a terminal shows the underlying error.

**An import used only inside markup is reported unused.** Ownership setup has not been
applied, or a project-level `pyrightconfig.json` or `[tool.pyright]` table overrides the
editor settings. Setup edits the effective configuration owner; rerun it after saving any
open configuration file.

**A genuinely unused import is not reported.** That is the other half of the contract and
should still appear, as `F401` from the pinned Ruff. If it does not, confirm
`python -m pysx.tooling.tools` resolves the expected Ruff and that it is installed in the
selected environment rather than only on `PATH`.

**Ruff disagrees between the editor and the command line.** Both must resolve the same
executable. PYSX never falls back to a `PATH` installation, so a mismatch means the editor
has a different environment selected than the shell.

**Wrong Ruff version reported.** PYSX rejects a version other than the pinned one rather
than using it. Install the pinned version into the selected environment.
