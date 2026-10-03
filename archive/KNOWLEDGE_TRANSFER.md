# pysx — knowledge transfer

Read this first in a fresh session. It carries the decisions, the constraints that
cost real debugging, and the state of the work. Open work lives in
[archive/TODO.md](archive/TODO.md); the wire format is
[archive/PROTOCOL.md](archive/PROTOCOL.md); the original build plan with every
verified environment fact is [archive/PLAN.md](archive/PLAN.md).

---

## What pysx is

A **server-side** reactive UI renderer for Python, in the spirit of Phoenix LiveView,
whose templates are **PEP 750 t-strings** containing an indentation-based markup DSL:

```python
@component
def app():
    count = signal(0)
    return html(t"""
        Page(id="container"):
            "Count: "; {count}
            Action(type="button", onClick={(lambda e: count.set(count() + 1))}):
                "Increment"
    """)
```

Signals live on the server, one graph per websocket connection. Events travel up,
minimal patch ops travel down. The browser runs ~90 lines of JavaScript.

### How this design was arrived at

It descends from **NTML**, a client-side reactive SPA renderer written in Nim (still
a separate, working project). The chain of reasoning, so it is not re-litigated:

1. Nim macros give NTML a compile-time DSL. Python has no macros.
2. Pure-Python tricks for that syntax — context managers, decorators, `__getitem__`,
   metaclass `__prepare__` — are all ugly in a way that matters when syntax is the
   point. They were rejected on those grounds.
3. Client-side Python needs Pyodide (megabytes) or a Python→JS compiler. Both kill
   the "lightweight" premise. **So pysx is server-side.** This is the single most
   load-bearing decision.
4. PEP 750 t-strings (Python 3.14) give the static/dynamic split for free, which is
   exactly what a compiled template needs. f-strings cannot work — they interpolate
   eagerly and destroy the holes before any library sees them.

---

## Status

Working: counter and todos examples, both with full headless and browser acceptance.

| Suite | Count |
|---|---|
| unit (`tests/test_*.py`) | 48 |
| headless acceptance | 16 |
| browser acceptance (Playwright) | 15 |
| TextMate grammar tokenization | 12 |

The todos example is a faithful port of NTML's `examples/todos.nim` and exercises
keyed lists, reactive attributes, two-way input binding, conditional blocks, and
scoped styles.

---

## Environment

- **Python 3.14+ is mandatory** — t-strings do not exist before it. `uv` fetches it.
- `uv` for everything. One runtime dependency: `websockets`.
- Node + Playwright only for browser tests; `vscode-textmate` only for grammar tests.
- Default port 8750 (`PSX_PORT`). Tests use 8751–8757.

```bash
./run.sh                                     # counter -> http://127.0.0.1:8750
PSX_APP=pysx.examples.todos:app ./run.sh     # todos
./install-extension.sh                       # VSCode highlighting + diagnostics
```

---

## Architecture

### The hole contract — read this before touching the parser

Every interpolation gets a kind, decided by **position and attribute name**, not by
value type:

| position | name | kind | rendered as |
|---|---|---|---|
| attribute | `on[A-Z]…` | `EVENT` | `data-pysx-{type}="hN"` |
| attribute | `bindValue` | `BIND` | `value="…"` + `data-pysx-input="hN"` |
| attribute | other | `ATTR` | element gets `data-pysx-el="eN"` |
| `if` header | — | `COND` | `<pysx-slot id="N">branch</pysx-slot>` |
| content | — | `TEXT` | `<pysx-slot id="N">value</pysx-slot>` |

**The parser consumes `onClick=` itself.** Inferring a hole's role from its Python
value instead produces `<button onClick=data-pysx-click="h1">` — an unquoted
attribute that swallows the marker, leaving the page silently inert.

A `TEXT` hole holding an `Each` renders as a keyed list. That *is* value-type
dispatch, and it is legitimate: the markup is identical either way, so the DSL owns
no structure there.

### The rule that surprises everyone

**Only a hole holding a bare `Signal` is reactive.** The component body runs once and
PEP 750 evaluates eagerly, so `{count()}`, `{count() * 2}` and
`{"even" if count() % 2 == 0 else "odd"}` freeze at their first value **with no
error**. `check.py` emits a diagnostic for the obvious case.

The same eagerness is why there is no DSL-level `for`: a loop variable cannot exist
when the hole is evaluated. Lists go through `each(source, Item, key=…)`, which the
renderer drives.

### Watchers

`render()` returns HTML plus a list of `Watcher`s — `Text`, `Cond`, `Attr`, `Class`,
`List`. Each reads its signal (registering the subscription) and emits an op **only
when its value actually changed**, so an event touching nothing sends no frame.

`ClassWatcher` exists because a `class` patch replaces the whole attribute: it must
carry the merged list (scoped hash + literals + hole), or the element loses its
styling on first interaction.

### One grammar, two adapters

`parser.py` is used by both the runtime and the checker, over **different text**:

- `check.py` slices **raw source** — sees a literal `\t`, a literal `{{`
- the runtime gets **decoded, hole-split** `Template.strings` — a real tab, one `{`

Never rebuild DSL text from decoded `Constant.value` for diagnostics; escapes and
doubled braces change length against the source and silently desync every range.

---

## Constraints that cost real debugging

- **Bare lambdas are a SyntaxError in holes** (`:` starts a format spec). Always
  `{(lambda e: …)}`. This is the number one paper cut.
- **A line whose only text is indentation, because a hole follows**, is a line
  *continuation*, not a blank line. Blanking it destroys the indent and the node
  reparents to column 0. This is why `{each(...)}` alone on a line once rendered
  outside its `<ul>`. See `template.py`.
- **Handler ids inside list items are derived**, `h{list}:{key}:{n}`, with `n`
  counting across the whole item — not per element. Counting per element made a
  checkbox and a Remove button share an id.
- **Item slot ids are namespaced** with the same prefix, or three rows all emit
  `<pysx-slot id="4">`.
- **CSS braces collide with interpolation.** `styled()` takes flat property lists
  (no braces); `global_style()` takes a plain `str`, not a t-string.
- **`value` and `checked` must be set as DOM properties**, not attributes, or an
  input the user already touched will not move.
- **Echo suppression**: the server drops `attr` ops targeting the element that
  produced the current event, or typing resets the caret on every keystroke.
- **Grammar highlighting is literal-call-site only** — `html(` and `t"""` must be on
  the same line. TextMate has no dataflow; there is no fix.
- **Nested reactivity works** via auto-tracking (a branch/item re-render subscribes
  the effect to whatever it reads). Coarse, not broken. Do not re-investigate.

---

## File map

```
src/pysx/
  reactive.py     signal / effect / derived, contextvars tracking
  template.py     t-string fragment dedent (the continuation-line rule)
  parser.py       DSL -> node tree + hole table
  styled.py       scoped-class hashing, global_style
  render.py       node tree -> HTML + watchers
  server.py       websockets, HTTP + WS on one port, Session
  check.py        JSON diagnostics CLI (python -m pysx.check FILE)
  static/         index.html + client.js (~90 lines)
  examples/       counter, todos
editor/           VSCode extension: injection grammar + diagnostics client
tests/            unit, acceptance (headless), browser (Playwright)
```

---

## Migration checklist — do these after the move

- [x] **`.gitignore` replaced.** The copied NTML one had blanket `*.js` / `*.html` /
      `.vscode` rules that dropped `client.js`, `index.html`, `extension.js` and the
      interpreter pin, while committing `__pycache__` and the `.vsix`. Verified by
      simulating a standalone repo: 41 files tracked, 0 artifacts.
- [ ] **Playwright.** `tests/browser*.mjs` import from `../../tests/node_modules/` —
      that was NTML's install and will not exist. Run `npm init -y && npm i -D
      playwright && npx playwright install chromium`, then fix the two import paths.
- [ ] **Grammar test harness is not in this folder.** `tokenize.mjs`, `inspect.mjs`
      and `fixtures/` live in NTML's `playground/pysx_tests/grammar/`. Copy them in
      (plus `vscode-textmate` and `vscode-oniguruma`) or grammar changes become
      unverifiable.
- [ ] **Delete `.venv` and regenerate.** It contains absolute paths to the old
      location. `uv run --project . python -c ""` rebuilds it.
- [ ] **`editor/pysx-root.json` bakes an absolute path** to the old checkout. It is
      now gitignored and `build_vsix.py` rewrites it on every build, so re-running
      `./install-extension.sh` self-heals it.
- [ ] **Remove NTML's repo-root `.vscode/settings.json`** — it points at
      `${workspaceFolder}/pysx/.venv`. `./uninstall-extension.sh` does this. The
      local `pysx/.vscode/settings.json` already uses `${workspaceFolder}/.venv`,
      which becomes correct once pysx is the workspace root.
- [ ] **Pylance grammar path is version-pinned** in the tokenizer
      (`ms-python.vscode-pylance-2026.4.1`). Glob it, or grammar tests break on the
      next Pylance update.

---

## Where this sits versus NTML

NTML is the more capable artifact today — routing, form bindings, lifecycle cleanup,
operator overloads, compile-time attribute typing, fourteen examples. It is also
client-side, which is architecturally stronger for what SPAs do.

pysx's case is reach, not technique: Python's audience versus Nim's, in a category
(server-driven Python UI) with proven demand whose incumbents — Reflex, NiceGUI,
FastHTML, Streamlit — all build markup at runtime. None of them compile templates.
PEP 750 is new enough that the space is unclaimed.

**The next thing to build is component composition in markup** — using a
`@component` as a tag with props. Every real app needs it by hour two, and it is
where eager evaluation could still bite, since props must survive into a child
template. If that comes out clean, the model holds. If it fights the way DSL-level
`for` did, the ceiling is lower than it currently looks.
