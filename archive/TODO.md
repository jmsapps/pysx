# pysx — open work

Scope: the counter and todos examples are the reference surface. Anything built on
top inherits their pitfalls, so these two should reach their logical endpoint first.

## Direction

**Tooling over compile-time enforcement.** NTML's `typedElements` fails the build on a
bad attribute; pysx's checker is advisory and a bad template still runs until first
render. That gap is accepted deliberately — JS is a runtime language too, and
TypeScript/JSX are equally advisory, so an editor-time checker is the mainstream bar
rather than a shortfall. Investment goes into diagnostics and editor feedback, not
into making the renderer refuse to start.

---

## 1. Example hardening

### Confirmed bugs

- [ ] **A handler exception closes the websocket.** `1 / 0` inside an `onClick`
      propagates out of `Session.dispatch`, escapes the `async for` in
      `server.py`, and ends the session. One bad handler kills the page.
      Catch per-dispatch, log server-side, and send the client an `error` op so
      the failure is visible instead of looking like a dead connection.
- [ ] **No empty-list state in todos.** Filtering to "Completed" with nothing done
      renders an empty `<ul>` and no explanation. NTML's original has the same
      gap, so this is an endpoint rather than a port defect.

### Known limits, deliberate

- [ ] **Item-internal changes re-send that item's whole HTML** rather than patching
      its individual slots (PROTOCOL.md). Keyed identity is preserved and an
      untouched row is never written, so this is correct at 3 rows and wrong at
      500. Sub-item slot granularity is the fix; cost is a per-item slot
      namespace and a second diff level. Decide before any list-heavy example.
- [ ] **No component composition in markup.** `each()` calls the item function
      directly, so a `@component` cannot be used as a tag with props. todos.nim
      happened not to need it; the next example will.

### Investigated, NOT bugs — do not re-open

- **Reactivity does nest into `if` branches and list items.** Auto-tracking
  subscribes the branch/list effect to every signal its re-render reads. The
  granularity is coarse (whole branch, whole item) but the behaviour is correct.
- **Session effects do not leak.** `Session.dispose()` drops subscriber counts to
  zero and the per-session graph is unreachable afterwards; the signal/effect
  cycle is collectable.

---

## 2. LSP diagnostics

Pylance sees the t-string as an opaque string — only the `{...}` holes are real AST
it type-checks. Every DSL-level rule is therefore exclusively ours to provide.

### Shipped

- unknown component tag
- bare lambda in a hole (with the parenthesise fix in the message)
- `{count()}` where a bare signal was meant — the silent freeze
- `html(f"""...""")` instead of `t"""`

### Next

- [ ] **Element/attribute typing — the `typedElements` port.** Validate attribute
      names against the element, reject incompatible value types, flag required
      ARIA pairings. This is NTML-8 re-expressed as live squiggles instead of
      build failures, and it is the single largest piece of the gap between the
      two projects.
- [ ] unknown attribute on a known element (`onlick=`)
- [ ] `each()` called without a `key`
- [ ] multi-line attribute lists (currently a parse error with a vague range)
- [ ] text on the opening `t"""` line
- [ ] indentation errors with exact ranges rather than a template-level marker

### Checker mechanics still owed

- [ ] Diagnostics are spawn-per-save (`python -m pysx.check`). Fine for one file,
      wrong for a large project — that is the honest reason a real LSP exists.
- [ ] Ranges are UTF-16 and derived from raw source slices. Any new diagnostic must
      keep that discipline: decoded `Constant.value` desyncs against the source on
      escapes and doubled braces.

---

## 3. Known environment constraints

- Grammar highlighting only applies at literal call sites — `html(` and `t"""` must
  be on the same line. Assigning a template to a variable first gets nothing.
  TextMate has no dataflow; there is no fix.
- Raw CSS blocks cannot live in a t-string (`{` opens an interpolation).
  `styled()` takes flat property lists; `global_style()` takes a plain `str`.
