"""Composition, identity, lifecycle, styling and accessible navigation."""

from tutorial.content import Lesson

LESSONS = (
    Lesson(
        8,
        "conditional-views",
        "Conditional views",
        "Compose an interface",
        "Choose a live branch and observe when its owned state is created or released.",
        "A conditional view is more than hidden text. Its branch may own inputs, state "
        "and effects. Hiding a DOM node and disposing a component are different operations. "
        "A lazy branch creates only the content that is currently selected.\n\n"
        "Use a readable condition in the template. Reading it eagerly into a plain bool "
        "before returning the fragment freezes that decision, just as an eager counter "
        "read froze the snapshot in lesson three.",
        "The DSL supports if/elif/else and matching, while when provides an explicit lazy "
        "construction helper. Do not build both expensive alternatives in Python and then "
        "choose between the already-built results.\n\n"
        "The demo keeps a parent count while mounting and removing a child. The child has "
        "its own local count and cleanup. Increment both, remove the child and restore it: "
        "the parent should retain its count while the new child starts fresh.",
        "from pysx import Fragment, pysx, signal\n\ndef app() -> Fragment:\n"
        '    visible = signal(True)\n    return pysx(t"""\n'
        '        if {visible}:\n            p: "Visible"\n'
        '        else:\n            p: "Hidden"\n    """)\n',
        "The condition is a live Signal. Only the selected body's nodes are active. "
        "The alternative is not an eagerly constructed component.",
        "Increment the child, hide it, show it again, and inspect the cleanup count.",
        "The child restarts at zero; the parent count stays. Cleanup increases when the "
        "child leaves.",
        "Choose branches lazily and make their lifetime boundaries explicit.",
        ("templates", "recovery"),
    ),
    Lesson(
        9,
        "lists-and-loops",
        "Lists and nested iteration",
        "Compose an interface",
        "Render live nested collections and keep each handler attached to its own row.",
        "A Python comprehension evaluated while building a template is a snapshot. A "
        "reactive list region instead reads its collection as that collection changes. "
        "This distinction matters when adding, removing or reordering records.\n\n"
        "Use each for live rows and each_indexed when the current position is part of "
        "the display. Nested rows need their own stable keys and correctly captured "
        "parent values; a callback must not accidentally use the last loop variable.",
        "A row renderer is an ordinary function. Its parameter provides one record and "
        "a closure can retain that record's identifier. The library owns reconciliation "
        "of the returned fragment. A stable key names the record, not its current index.\n\n"
        "In the demo, reverse the groups before selecting a nested child. The selection "
        "readout should still name the clicked group's child. Adding a group affects the "
        "live region, while the snapshot count remains the count taken at setup.",
        "from pysx import Fragment, each, pysx, signal\n\ndef app() -> Fragment:\n"
        '    rows = signal(["Read", "Build"])\n'
        "    def row(text: str) -> Fragment:\n        return pysx(t'li: {text}')\n"
        "    return pysx(t'ul: {each(rows, row, key=str)}')\n",
        "The source is passed to each uncalled. The row function receives the current "
        "value, and key=str is suitable here because the small example has unique text keys.",
        "Add a group, reverse the groups and select a child from the original first group.",
        "The list changes immediately, the snapshot stays fixed and selection names the "
        "correct group.",
        "A live list is an owned region, not a comprehension rerun by magic.",
        ("templates",),
    ),
    Lesson(
        10,
        "keyed-identity",
        "Stable keys and retained identity",
        "Compose an interface",
        "Keep a draft, component state and keyboard focus attached to the same record.",
        "A key tells reconciliation which record survived an update. If you key by "
        "position, the first slot stays the first slot even when a different record moves "
        "into it. A stable record key lets the existing DOM and its local state move together.\n\n"
        "Record identity and record value are separate. A same-key replacement can rename "
        "a row without discarding its input or callbacks. Removing a key, however, ends "
        "that row's ownership; restoring the same key later starts a fresh lifetime.",
        "The demo uses component-local state inside each keyed row. Edit the first draft "
        "and use Focus and reverse, which focuses its owned input before changing order. "
        "The node should move instead of being recreated.\n\n"
        "This is a behavioral contract worth testing with object identity, not just "
        "matching visible strings. An implementation can reconstruct identical HTML "
        "while still losing caret position, selection or a native control's state.",
        "from pysx import Fragment, each, local_state, pysx, signal\n\n"
        "def app() -> Fragment:\n    rows = signal([1, 2])\n"
        '    def row(key: int) -> Fragment:\n        draft = local_state("draft", "")\n'
        "        return pysx(t'\\nli:\\n    input(bindValue={draft})\\n')\n"
        "    return pysx(t'ul: {each(rows, row, key=lambda key: key)}')\n",
        "The stable integer key identifies the record. draft belongs to that row owner, "
        "so movement retains it and removal disposes it.",
        "Type into Alpha, increment its count, focus and reverse, then remove and restore Alpha.",
        "Movement keeps the draft/count/focus. Removal followed by restoration creates an "
        "empty draft.",
        "Keys preserve surviving identities; they do not resurrect a disposed component.",
        ("keyed_rows",),
    ),
    Lesson(
        11,
        "components-and-children",
        "Components, props and children",
        "Compose an interface",
        "Compose reusable typed callables without sharing private instance state.",
        "A component is a Python callable that returns content. Props describe the "
        "inputs it accepts; children provide caller-owned content to place inside its "
        "layout. Components let one clear interface hide markup details.\n\n"
        "Import components normally and use their PascalCase names in markup. The "
        "scoped compiler records lexical references, including names used only as tags. "
        "Keep the application loader installed before importing template-producing modules.",
        "The shared card below accepts a heading and arbitrary children. Two instances "
        "each own a count; changing the shared heading updates their presentation without "
        "turning the counts into one shared variable.\n\n"
        "A styled callable can extend a component's appearance while preserving its "
        "content and prop contract. Children retain the caller's namespace: a component "
        "does not gain permission to reinterpret a caller's dynamic source.",
        "from pysx import Children, Fragment, pysx\n\n"
        "def Card(*, title: str, children: Children | None = None) -> Fragment:\n"
        "    return pysx(t'\\nsection:\\n    h2: {title}\\n    div: {children}\\n')\n\n"
        'def app() -> Fragment:\n    return pysx(t\'Card(title="A reusable card"): "Content"\')\n',
        "title is a typed prop and children is composable content. The caller controls "
        "the content without duplicating the section and heading structure.",
        "Change the common heading and increment only the first component instance.",
        "Both headings change, but only the first instance's local count increments.",
        "Share component definitions, not accidental instance state.",
        ("composition",),
    ),
    Lesson(
        12,
        "effects-and-lifetimes",
        "Effects and component ownership",
        "Compose an interface",
        "Observe changes and prove that cleanup ends an owned subscription.",
        "An effect runs when the dependencies it read change. It is useful for work "
        "that must happen because state changed, rather than for computing another value. "
        "Return cleanup from an effect to release the previous run's resources.\n\n"
        "Server setup, browser mount and disposal are different moments. Setup can happen "
        "before a browser attaches. A DOM command needs a mounted capability. Cleanup must "
        "still run when a branch leaves or its entire session closes.",
        "own_effect ties a keyed observer to a component. Direct effect calls return "
        "a disposer that the caller must own. Similarly, prefer owned timers/tasks and "
        "subscriptions over unmanaged background factories.\n\n"
        "The bounded log below records run N and cleanup N. Incrementing should clean "
        "up the old run before recording the new run. Once the observer is removed, "
        "further increments must not append log entries until a new observer is attached.",
        "from pysx import effect, signal\n\ncount = signal(0)\n"
        "seen: list[int] = []\nobserver = effect(lambda: seen.append(count()))\n"
        "count.set(1)\nobserver.dispose()\ncount.set(2)\nassert seen == [0, 1]\n",
        "The effect subscribes on its first read. dispose removes that subscription, "
        "which is why changing the source to two does not produce another entry.",
        "Increment, dispose the observer, increment again and reattach it. Reset the log.",
        "Cleanup precedes rerun. A disposed observer produces no new entries.",
        "Every effect, timer and task needs an owner and a definite end to its lifetime.",
        ("effects", "composition", "resources"),
    ),
    Lesson(
        13,
        "styles-and-themes",
        "Styles, variables and themes",
        "Compose an interface",
        "Change presentation reactively without replacing the styled DOM node.",
        "A styled definition can be shared across instances. Its dynamic values and "
        "active theme belong to a render/session. This separation keeps reusable style "
        "rules from becoming a process-global theme toggle for every learner.\n\n"
        "Use inherited native or callable styles for common surfaces. CSS variables "
        "carry reactive values through those rules; named themes provide a coherent "
        "set of tokens. Literal property lists avoid raw CSS braces in t-strings.",
        "The preview below uses the course's own shared theme definitions, while its "
        "spacing is local to this demo. A theme switch updates computed appearance; it "
        "should not remount the input or discard its draft.\n\n"
        "Empty dynamic variable values remove the local override, allowing the declared "
        "fallback to apply. css() is for validated flat snapshot CSS text; it is not "
        "an unrestricted live stylesheet or a way to execute submitted source.",
        "from pysx import Fragment, pysx, styled\n\n"
        'Notice = styled.p(t"padding: 12px; color: teal;")\n\n'
        "def app() -> Fragment:\n    return pysx(t'Notice: \"A scoped style\"')\n",
        "Notice is a styled native paragraph. A real lexical reference lets the compiler "
        "and editor recognize it, while the renderer owns the scoped style rule.",
        "Change spacing and switch theme. Type into the preview before another theme switch.",
        "Computed spacing/colors change while the preview input and its draft remain intact.",
        "Share definitions; keep dynamic themes and values scoped to the learner.",
        ("themes", "composition"),
    ),
    Lesson(
        14,
        "routes-and-parameters",
        "Routes, parameters and deep links",
        "Navigation",
        "Match a URL and keep route parameters live within an owned branch.",
        "A route gives a URL a view and a lifetime. Parameters identify the record "
        "being displayed, while exact, nested, wildcard and fallback matches decide which "
        "branch should exist. A deep link must work on the initial HTTP request too.\n\n"
        "Routers in one document share its real location. They are not independent toy "
        "browsers. This course deliberately places its routing exercise under the lesson's "
        "URL so it cannot silently compete with unrelated course routes.",
        "RouteState provides readable params and location. A compatible parameter "
        "change can retain a branch's local state; navigating away ends its owned work. "
        "Do not confuse that retention with global persistence.\n\n"
        "Use the project links below and watch the actual address bar. Copy a project "
        "URL into a fresh tab. The lesson and selected project should render immediately "
        "without first visiting the course overview.",
        "from pysx import Fragment, Route, Router, RouteState, derived, pysx\n\n"
        "def project(state: RouteState) -> Fragment:\n"
        '    identifier = derived(lambda: state.params()["id"])\n'
        "    return pysx(t'h2: {identifier}')\n\n"
        'def app() -> Fragment:\n    router = Router([Route("/projects/:id", project)])\n'
        "    return pysx(t'main: {router.view()}')\n",
        "The :id segment names a parameter. The branch receives RouteState instead of "
        "parsing raw URL strings itself. Its content is mounted through router.view().",
        "Open project one and then project two, inspect the URL, and reload the second deep link.",
        "The selected project matches the URL before and after reload. Course navigation "
        "still works.",
        "A route is both a URL match and an ownership boundary.",
        ("navigation",),
    ),
    Lesson(
        15,
        "links-and-history",
        "Links, history and navigation",
        "Navigation",
        "Use browser history and relative navigation without breaking native anchors.",
        "A navigation link still needs to behave like a link. A modified click, a "
        "download, another target or an external URL has native browser meaning. Internal "
        "navigation may be intercepted only when that ordinary anchor action is eligible.\n\n"
        "Relative paths use familiar filesystem-style ./ and ../ resolution. A query "
        "or fragment update changes a different part of the current location. Push adds "
        "history; replace changes the current entry.",
        "Link composes a normal callback with the router's navigation decision. A "
        "NavigationEvent may explicitly cancel that decision; cancelling is different "
        "from changing the destination after navigation already happened.\n\n"
        "The controls below change the actual lesson URL. Use Back and Forward after "
        "moving between project details. A replace-query control should not create an "
        "extra history entry, while a cancelled destination should leave the URL alone.",
        "from pysx import Fragment, Link, Route, Router, RouteState, pysx\n\n"
        "def home(_: RouteState) -> Fragment:\n    return pysx(t'p: \"Home\"')\n\n"
        'def app() -> Fragment:\n    router = Router([Route("/", home)])\n'
        '    return pysx(t\'main: {Link("Home", router=router, href="/")} {router.view()}\')\n',
        "Link creates a real anchor associated with its router. Keeping the href "
        "meaningful preserves copying, native targets and browser navigation conventions.",
        "Push a detail URL, replace its query, go Back and Forward, then try a cancelled link.",
        "The address bar and readout agree. Cancellation changes neither the destination "
        "nor history.",
        "Use real links, deliberate history semantics and browser-local native exceptions.",
        ("navigation",),
    ),
    Lesson(
        16,
        "keyboard-and-focus",
        "Accessible keyboard and DOM capabilities",
        "Navigation",
        "Operate an accessible chooser with keys and an owned focus command.",
        "Accessibility comes from behavior as well as attributes. A combobox needs "
        "an expanded state, an active option and predictable keys. A roving control needs "
        "one tab stop and actual focus movement, not only a highlighted background.\n\n"
        "The server cannot synchronously inspect arbitrary browser nodes. Dom gives "
        "an owned ref, and its handle exposes bounded asynchronous capabilities after "
        "mount. A command must stay inside the component's ownership boundary.",
        "The chooser below keeps input focus while ArrowUp/ArrowDown changes the active "
        "option. Enter selects, and Escape closes. ARIA state follows the same sources "
        "as the visible state so the two cannot drift apart.\n\n"
        "A focus button awaits the ref's handle. Preventing the arrow key's native "
        "default is declared in on_event, before the callback travels to Python. Try "
        "Tab and Shift+Tab as well as the chooser's own keys.",
        "from pysx import Dom, Fragment, pysx\n\ndef app() -> Fragment:\n"
        "    dom = Dom()\n    field = dom.ref()\n"
        "    async def focus(_: object) -> None:\n        await field.handle().focus()\n"
        '    return pysx(t\'input(ref={field} ariaLabel="Name"); button(onClick={focus}): '
        '"Focus name"\')\n',
        "The ref is created by this component. The command is awaited from an async "
        "handler after mounting, rather than pretending a server-side object is a DOM node.",
        "Focus the chooser, press Down twice and Enter, then use Escape and the focus button.",
        "Selection, active-descendant and expanded state agree, and the focus button "
        "focuses the input.",
        "Test keyboard focus and ARIA together; an owned ref grants only bounded browser access.",
        ("events", "composition"),
    ),
)
