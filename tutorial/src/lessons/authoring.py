"""Composition, identity, lifecycle, styling and accessible navigation."""

from textwrap import dedent

from src.content import Lesson

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
        "The DSL supports `if`/`elif`/`else` and matching, while `when` provides an explicit lazy "
        "construction helper. Do not build both expensive alternatives in Python and then "
        "choose between the already-built results.\n\n"
        "The demo keeps a parent count while mounting and removing a child. The child has "
        "its own local count and cleanup. Increment both, remove the child and restore it: "
        "the parent should retain its count while the new child starts fresh.",
        dedent('''
            from pysx import Fragment, local_state, on_cleanup, pysx, signal


            def app() -> Fragment:
                visible = signal(True)
                parent = signal(0)
                cleanups = signal(0)

                def Child() -> Fragment:
                    count = local_state("child-count", 0)
                    on_cleanup(lambda: cleanups.update(lambda value: value + 1))

                    def increment(_: object) -> None:
                        count.update(lambda value: value + 1)

                    return pysx(t"""
                        section(id="conditional-child")
                            button(id="child-increment" onClick={increment}): "Increment child"
                            output(id="child-count"): {count}
                    """)

                def toggle(_: object) -> None:
                    visible.set(not visible())

                def increment(_: object) -> None:
                    parent.update(lambda value: value + 1)

                return pysx(t"""
                    div:
                        button(id="branch-toggle" onClick={toggle}): "Hide or show child"
                        button(id="parent-increment" onClick={increment}): "Increment parent"
                    output(id="parent-count"): {parent}
                    output(id="branch-cleanups"): {cleanups}
                    if {visible}:
                        Child:
                    else:
                        p(id="child-absent"): "The child owner has been disposed."
                """)
        ''').strip(),
        "`visible` selects the child lifetime. The parent count lives outside that branch. "
        "Removing the child runs its cleanup; showing it again creates a new local count at zero "
        "while the parent retains its value.",
        "Increment the parent and child, hide the child, show it again, and inspect the "
        "cleanup count.",
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
        "Use `each` for live rows and `each_indexed` when the current position is part of "
        "the display. Nested rows need their own stable keys and correctly captured "
        "parent values; a callback must not accidentally use the last loop variable.",
        "A row renderer is an ordinary function. Its parameter provides one record and "
        "a closure can retain that record's identifier. The library owns reconciliation "
        "of the returned fragment. A stable key names the record, not its current index.\n\n"
        "In the demo, reverse the groups before selecting a nested child. The selection "
        "readout should still name the clicked group's child. Adding a group affects the "
        "live region, while the snapshot count remains the count taken at setup.",
        dedent('''
            from pysx import Fragment, each, pysx, signal


            def app() -> Fragment:
                groups = signal(["Read", "Build"])
                snapshot = len(groups())
                selected = signal("None")

                def group(name: str) -> Fragment:
                    def child(label: str) -> Fragment:
                        def choose(_: object) -> None:
                            selected.set(f"{name} / {label}")

                        choice_id = name + "-" + label

                        return pysx(t"""
                            button(data-choice={choice_id} onClick={choose}): {label}
                        """)

                    return pysx(t"""
                        li(data-group={name})
                            h3: {name}
                            div: {each(("first", "second"), child, key=str)}
                    """)

                def add(_: object) -> None:
                    if len(groups()) < 12:
                        groups.update(lambda rows: [*rows, f"Group {len(rows) + 1}"])

                def reverse(_: object) -> None:
                    groups.update(lambda rows: list(reversed(rows)))

                return pysx(t"""
                    div:
                        button(id="group-add" onClick={add}): "Add group"
                        button(id="group-reverse" onClick={reverse}): "Reverse groups"
                    ul(id="groups"): {each(groups, group, key=str)}
                    output(id="group-selection"): {selected}
                    p: "Snapshot group count: " {snapshot}
                """)
        ''').strip(),
        "`groups` is passed uncalled to `each`. Every nested handler closes over its own group "
        "name and label. Add and reverse update the live list; `snapshot` stays at its initial "
        "length of two.",
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
        dedent('''
            from dataclasses import dataclass
            from pysx import Dom, Fragment, each, local_state, pysx, signal


            @dataclass(frozen=True)
            class Row:
                key: str
                label: str


            def app() -> Fragment:
                initial = (Row("alpha", "Alpha"), Row("beta", "Beta"))
                rows = signal(list(initial))
                dom = Dom()

                def row(item: Row) -> Fragment:
                    draft = local_state("draft", "")
                    count = local_state("count", 0)
                    ref = dom.ref()

                    def increment(_: object) -> None:
                        count.update(lambda value: value + 1)

                    async def move(_: object) -> None:
                        await ref.handle().focus()
                        rows.update(lambda values: list(reversed(values)))

                    return pysx(t"""
                        li(data-row={item.key})
                            label(for={"draft-" + item.key}): {item.label}
                            input(id={"draft-" + item.key} ref={ref} bindValue={draft})
                            div:
                                button(data-increment={item.key} onClick={increment}): "Count:
                                    " {count}
                                button(data-move={item.key} onClick={move}): "Focus and reverse"
                    """)

                def remove(_: object) -> None:
                    rows.update(lambda values: [item for item in values if item.key != "alpha"])

                def restore(_: object) -> None:
                    if not any(item.key == "alpha" for item in rows()):
                        rows.update(lambda values: [initial[0], *values])

                return pysx(t"""
                    ul(id="keyed-rows"): {each(rows, row, key=lambda item: item.key)}
                    div:
                        button(id="row-remove" onClick={remove}): "Remove Alpha"
                        button(id="row-restore" onClick={restore}): "Restore Alpha"
                """)
        ''').strip(),
        "`Row.key` is alpha or beta, independent of position. Each row owns its draft, count and "
        "DOM ref. Focus and reverse moves surviving rows; removing Alpha disposes its local "
        "state, so restoration starts with an empty draft and zero count.",
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
        dedent('''
            from pysx import Children, Fragment, local_state, pysx, signal


            def app() -> Fragment:
                title = signal("Shared heading")

                def Card(*, number: int, children: Children | None = None) -> Fragment:
                    count = local_state("count", 0)

                    def increment(_: object) -> None:
                        count.update(lambda value: value + 1)

                    return pysx(t"""
                        section(data-instance={number})
                            h3: {title}
                            {children}
                            button(data-card-increment={number} onClick={increment}):
                                "Increment instance"
                            output(data-card-count={number}): {count}
                    """)

                return pysx(t"""
                    label(for="component-title"): "Heading shared by both component instances"
                    input(id="component-title" bindValue={title})
                    Card(number={1}): "Caller-owned content for the first card."
                    Card(number={2}): "Different children, independent local state."
                """)
        ''').strip(),
        "Both Card instances read the same heading signal and receive different caller-owned "
        "children. Their keyed local count belongs to each component instance, so incrementing "
        "the first leaves the second unchanged.",
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
        "`own_effect` ties a keyed observer to a component. Direct `effect` calls return "
        "a disposer that the caller must own. Similarly, prefer owned timers/tasks and "
        "subscriptions over unmanaged background factories.\n\n"
        "The bounded log below records run N and cleanup N. Incrementing should clean "
        "up the old run before recording the new run. Once the observer is removed, "
        "further increments must not append log entries until a new observer is attached.",
        dedent('''
            from collections.abc import Callable
            from pysx import Fragment, own_effect, pysx, signal


            def app() -> Fragment:
                count = signal(0)
                active = signal(True)
                log = signal("")
                entries: list[str] = []

                def record(text: str) -> None:
                    entries.append(text)
                    del entries[:-10]
                    log.set(" | ".join(entries))

                def Observer() -> Fragment:
                    def observe() -> Callable[[], None]:
                        value = count()
                        record(f"run {value}")

                        return lambda: record(f"cleanup {value}")

                    own_effect("lesson-observer", observe)

                    return pysx(t"""
                        p(id="observer-active"): "Observer attached"
                    """)

                def increment(_: object) -> None:
                    count.update(lambda value: value + 1)

                def toggle(_: object) -> None:
                    active.set(not active())

                return pysx(t"""
                    div:
                        button(id="effect-increment" onClick={increment}): "Increment source"
                        button(id="effect-toggle" onClick={toggle}): "Attach or dispose observer"
                    output(id="effect-source"): {count}
                    if {active}:
                        Observer:
                    output(id="effect-log" role="log"): {log}
                """)
        ''').strip(),
        "`own_effect` owns the observer inside the conditional component. Its callback records "
        "run N and returns a cleanup that records cleanup N before rerun or disposal. While "
        "detached, changing the source cannot append a run; reattachment observes the current "
        "value. Only the last ten entries are retained.",
        "Increment, dispose the observer, increment again and reattach it. Use Reset demo "
        "to start a new log.",
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
        "fallback to apply. `css()` is for validated flat snapshot CSS text; it is not "
        "an unrestricted live stylesheet or a way to execute submitted source.",
        dedent('''
            from pysx import Fragment, Themes, derived, pysx, signal


            def selected_name(themes: Themes) -> str:
                current = themes.current()

                return current.name if current is not None else "light"


            def styles(themes: Themes) -> Fragment:
                spacing = signal("16px")
                draft = signal("")
                variables = {"preview-space": spacing}
                name = derived(lambda: selected_name(themes))

                def compact(_: object) -> None:
                    spacing.set("8px" if spacing() == "16px" else "16px")

                def switch(_: object) -> None:
                    themes.select("dark" if name() == "light" else "light")

                return pysx(t"""
                    div:
                        button(id="preview-spacing" onClick={compact}): "Change spacing"
                        button(id="preview-theme" onClick={switch}): "Switch preview theme"
                    section(id="style-preview" styleVars={variables}
                        style="padding: var(--preview-space)")
                        label(for="style-draft"): "Keep this input while changing presentation"
                        input(id="style-draft" bindValue={draft})
                    output(id="preview-theme-name"): {name}
                """)


            def app() -> Fragment:
                themes = Themes()
                themes.register("light", {"paper": "#f7f9fe", "ink": "#102449"})
                themes.register("dark", {"paper": "#09162d", "ink": "#e8efff"})
                themes.select("light")
                preview = styles(themes)

                return pysx(
                    t"""
                    section(style="background: var(--paper); color: var(--ink)")
                        {preview}
                """,
                    themes=themes,
                )
        ''').strip(),
        "`spacing` starts at 16px and supplies `--preview-space`; Change spacing toggles it to "
        "8px. The theme button switches the named light/dark themes. Both updates retain the "
        "input and its draft. The course uses its full shared palette; this standalone version "
        "keeps the same paper/ink tokens and native controls.",
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
        "`RouteState` provides readable `params` and `location`. A compatible parameter "
        "change can retain a branch's local state; navigating away ends its owned work. "
        "Do not confuse that retention with global persistence.\n\n"
        "Use the project links below and watch the actual address bar. Copy a project "
        "URL into a fresh tab. The lesson and selected project should render immediately "
        "without first visiting the course overview.",
        dedent('''
            from pysx import (
                Fragment,
                Link,
                NavigationEvent,
                Route,
                RouteState,
                Router,
                derived,
                project,
                pysx,
                signal,
            )


            def routes(router: Router, slug: str, state: RouteState) -> Fragment:
                path = "/learn/" + slug
                location = derived(lambda: router.location().url)
                notice = signal("No cancelled navigation")
                project = derived(lambda: state.params().get("id", "No project selected"))
                visits = signal(0)

                def increment(_: object) -> None:
                    visits.update(lambda value: value + 1)

                def cancel(event: NavigationEvent) -> None:
                    event.cancel_navigation()
                    notice.set("Navigation cancelled")

                return pysx(t"""
                    div:
                        Link(router={router} href={path + "/projects/1"} id="route-one"):
                            "Project one"
                        Link(router={router} href={path + "/projects/2"} id="route-two"):
                            "Project two"
                        Link(router={router} href="?tab=details" replace={True} id="route-query"):
                            "Replace query"
                        Link(router={router} href={path} id="route-reset"): "Lesson root"
                        Link(router={router} href={path + "/files/docs/start"} id="route-files"):
                            "Wildcard files"
                        Link(router={router} href={path + "/not-a-project"} id="route-missing"):
                            "Lesson fallback"
                        Link(router={router} href={path + "/cancelled"} on_navigate={cancel}
                            id="route-cancel"): "Cancel this navigation"
                        Link(router={router} href={path} target="_blank" id="route-native"):
                            "Open lesson in another tab"
                    output(id="route-location"): {location}
                    output(id="route-project"): {project}
                    button(id="route-increment" onClick={increment}): "Increment route-local count"
                    output(id="route-count"): {visits}
                    output(id="route-notice"): {notice}
                """)


            def app() -> Fragment:
                def lesson(state: RouteState) -> Fragment:
                    return routes(router, "routes-and-parameters", state)

                router = Router(
                    [
                        Route("/", lesson),
                        Route("/learn/routes-and-parameters", lesson),
                        Route("/learn/routes-and-parameters" + "/projects/:id", lesson),
                        Route("/learn/routes-and-parameters" + "/files/*path", lesson),
                        Route("/learn/routes-and-parameters" + "/*missing", lesson),
                    ]
                )

                return pysx(t"""
                    main: {router.view()}
                """)
        ''').strip(),
        "The project links match the :id route and `state.params()` supplies the live project "
        "readout. Both project URLs use the same lesson handler, including on a direct deep-link "
        "load. The standalone version includes the lesson paths without the surrounding course "
        "navigation.",
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
        "Relative paths use familiar filesystem-style `./` and `../` resolution. A query "
        "or fragment update changes a different part of the current location. Push adds "
        "history; replace changes the current entry.",
        "`Link` composes a normal callback with the router's navigation decision. A "
        "`NavigationEvent` may explicitly cancel that decision; cancelling is different "
        "from changing the destination after navigation already happened.\n\n"
        "The controls below change the actual lesson URL. Use Back and Forward after "
        "moving between project details. A replace-query control should not create an "
        "extra history entry, while a cancelled destination should leave the URL alone.",
        dedent('''
            from pysx import (
                Fragment,
                Link,
                NavigationEvent,
                Route,
                RouteState,
                Router,
                derived,
                project,
                pysx,
                signal,
            )


            def routes(router: Router, slug: str, state: RouteState) -> Fragment:
                path = "/learn/" + slug
                location = derived(lambda: router.location().url)
                notice = signal("No cancelled navigation")
                project = derived(lambda: state.params().get("id", "No project selected"))
                visits = signal(0)

                def increment(_: object) -> None:
                    visits.update(lambda value: value + 1)

                def cancel(event: NavigationEvent) -> None:
                    event.cancel_navigation()
                    notice.set("Navigation cancelled")

                return pysx(t"""
                    div:
                        Link(router={router} href={path + "/projects/1"} id="route-one"):
                            "Project one"
                        Link(router={router} href={path + "/projects/2"} id="route-two"):
                            "Project two"
                        Link(router={router} href="?tab=details" replace={True} id="route-query"):
                            "Replace query"
                        Link(router={router} href={path} id="route-reset"): "Lesson root"
                        Link(router={router} href={path + "/files/docs/start"} id="route-files"):
                            "Wildcard files"
                        Link(router={router} href={path + "/not-a-project"} id="route-missing"):
                            "Lesson fallback"
                        Link(router={router} href={path + "/cancelled"} on_navigate={cancel}
                            id="route-cancel"): "Cancel this navigation"
                        Link(router={router} href={path} target="_blank" id="route-native"):
                            "Open lesson in another tab"
                    output(id="route-location"): {location}
                    output(id="route-project"): {project}
                    button(id="route-increment" onClick={increment}): "Increment route-local count"
                    output(id="route-count"): {visits}
                    output(id="route-notice"): {notice}
                """)


            def app() -> Fragment:
                def lesson(state: RouteState) -> Fragment:
                    return routes(router, "links-and-history", state)

                router = Router(
                    [
                        Route("/", lesson),
                        Route("/learn/links-and-history", lesson),
                        Route("/learn/links-and-history" + "/projects/:id", lesson),
                        Route("/learn/links-and-history" + "/files/*path", lesson),
                        Route("/learn/links-and-history" + "/*missing", lesson),
                    ]
                )

                return pysx(t"""
                    main: {router.view()}
                """)
        ''').strip(),
        "Project links push real destinations. Replace query uses `replace=True`; the cancelled "
        "link calls `cancel_navigation()` before changing history. The location readout derives "
        "from the same router. The standalone version uses the same lesson paths without the "
        "surrounding course navigation.",
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
        "The server cannot synchronously inspect arbitrary browser nodes. `Dom` gives "
        "an owned ref, and its handle exposes bounded asynchronous capabilities after "
        "mount. A command must stay inside the component's ownership boundary.",
        "The chooser below keeps input focus while `ArrowUp`/`ArrowDown` changes the active "
        "option. `Enter` selects, and `Escape` closes. ARIA state follows the same sources "
        "as the visible state so the two cannot drift apart.\n\n"
        "A focus button awaits the ref's handle. Preventing the arrow key's native "
        "default is declared in `on_event`, before the callback travels to Python. Try "
        "Tab and Shift+Tab as well as the chooser's own keys.",
        dedent('''
            from dataclasses import dataclass
            from pysx import BrowserEvent, Dom, Fragment, derived, each, on_event, pysx, signal


            @dataclass(frozen=True)
            class TreeEntry:
                key: str
                label: str
                children: tuple[TreeEntry, ...] = ()


            def tree_controls() -> Fragment:
                root = TreeEntry(
                    "root",
                    "Projects",
                    (
                        TreeEntry(
                            "read",
                            "Read",
                        ),
                        TreeEntry(
                            "build",
                            "Build",
                        ),
                    ),
                )
                dom = Dom()
                refs = {key: dom.ref() for key in ("root", "read", "build")}
                opened = signal(False)
                active = signal("root")
                chosen = signal("")
                labels = {"root": "Projects", "read": "Read", "build": "Build", "": "None"}
                selection = derived(lambda: labels[chosen()])

                def node(entry: TreeEntry, level: int = 1) -> Fragment:
                    async def keyboard_event(event: BrowserEvent) -> None:
                        shown = ["root", "read", "build"] if opened() else ["root"]
                        index = shown.index(entry.key)
                        destination = entry.key

                        if event.key == "ArrowRight" and entry.children:
                            if opened():
                                destination = entry.children[0].key
                            else:
                                opened.set(True)
                        elif event.key == "ArrowLeft":
                            if entry.key == "root":
                                opened.set(False)
                            destination = "root"
                        elif event.key in ("ArrowDown", "ArrowUp"):
                            destination = shown[
                                max(
                                    0,
                                    min(
                                        len(
                                            shown,
                                        )
                                        - 1,
                                        index + (1 if event.key == "ArrowDown" else -1),
                                    ),
                                )
                            ]
                        elif event.key in ("Home", "End"):
                            destination = shown[0 if event.key == "Home" else -1]
                        elif event.key == "Enter":
                            chosen.set(entry.key)
                        active.set(destination)
                        await refs[destination].handle().focus()

                    key_handler = on_event(
                        keyboard_event,
                        keys=(
                            "ArrowRight",
                            "ArrowLeft",
                            "ArrowDown",
                            "ArrowUp",
                            "Home",
                            "End",
                            "Enter",
                        ),
                        prevent_default=True,
                        stop_propagation=True,
                    )
                    tab_stop = derived(lambda: 0 if active() == entry.key else -1)
                    expanded = opened if entry.children else None
                    selected = derived(lambda: chosen() == entry.key)
                    children = each(
                        derived(lambda: entry.children),
                        lambda child: node(child, level + 1),
                        key=lambda child: child.key,
                    )

                    return pysx(t"""
                        li(id={"lesson-tree-" + entry.key} ref={refs[entry.key]} role="treeitem"
                            tabindex={tab_stop} aria-expanded={expanded} aria-level={level}
                            aria-selected={selected} onKeydown={key_handler})
                            span: {entry.label}
                            if {bool(entry.children)}:
                                if {opened}:
                                    ul(role="group")
                                        {children}
                    """)

                return pysx(t"""
                    ul(id="lesson-tree" role="tree" aria-label="Project learning tree")
                        {node(root)}
                    output(id="tree-selection"): {selection}
                """)


            def app() -> Fragment:
                dom = Dom()
                ref = dom.ref()
                active = signal(0)
                opened = signal(False)
                selected = signal("None")
                labels = ("Read", "Build", "Ship")
                descendant = derived(lambda: f"choice-{active()}")

                def key(event: BrowserEvent) -> None:
                    if event.key in ("ArrowDown", "ArrowUp"):
                        active.set(
                            (active() + (1 if event.key == "ArrowDown" else -1))
                            % len(
                                labels,
                            ),
                        )
                        opened.set(True)
                    elif event.key == "Enter":
                        selected.set(labels[active()])
                        opened.set(False)
                    elif event.key == "Escape":
                        opened.set(False)

                async def focus(_: object) -> None:
                    await ref.handle().focus()

                key_handler = on_event(
                    key, keys=("ArrowDown", "ArrowUp", "Enter", "Escape"), prevent_default=True
                )
                tree = tree_controls()

                return pysx(t"""
                    label(for="chooser"): "Choose a project stage"
                    input(id="chooser" role="combobox" ref={ref} ariaControls="choices"
                        ariaExpanded={opened} ariaActivedescendant={descendant}
                        onKeydown={key_handler})
                    ul(id="choices" role="listbox" hidden={derived(lambda: not opened())})
                        li(id="choice-0" role="option"
                            ariaSelected={derived(lambda: active() == 0)}):
                            "Read"
                        li(id="choice-1" role="option"
                            ariaSelected={derived(lambda: active() == 1)}):
                            "Build"
                        li(id="choice-2" role="option"
                            ariaSelected={derived(lambda: active() == 2)}):
                            "Ship"
                    button(id="focus-chooser" onClick={focus}): "Focus chooser"
                    output(id="chooser-selection"): {selected}
                    h3: "A recursive tree with one tab stop"
                    {tree}
                """)
        ''').strip(),
        "The chooser keeps focus on its input while arrow keys update the active option, Enter "
        "selects and Escape closes it. ARIA attributes read the same state. The awaited ref "
        "command focuses the input. The second control applies owned refs and a single roving tab "
        "stop to a recursive tree.",
        "Focus the chooser, press Down twice and Enter, then use Escape and the focus button.",
        "Selection, active-descendant and expanded state agree, and the focus button "
        "focuses the input.",
        "Test keyboard focus and ARIA together; an owned ref grants only bounded browser access.",
        ("events", "composition"),
    ),
)
