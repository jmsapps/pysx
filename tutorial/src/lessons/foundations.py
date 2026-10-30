"""From a running application to useful reactive state."""

from textwrap import dedent

from src.content import Lesson

LESSONS = (
    Lesson(
        1,
        "getting-started",
        "Install and run your first application",
        "Foundations",
        "Run a Python application and identify which work happens on the server.",
        "A live interface starts with an ordinary Python callable. The callable returns a "
        "fragment describing the page; pysx renders it to HTML, then connects the browser "
        "to the server for later events. You do not need a JavaScript application toolchain "
        "to make your first button work.\n\n"
        "Use Python 3.14 or newer: t-strings preserve expressions instead of immediately "
        "flattening them into text. The environment panel below reports the interpreter "
        "actually running this course. It is not a browser simulation of Python.",
        "The panel above copies a runnable counter into your own project and serves it. "
        "That counter is an ordinary file you own; nothing about it is special to this "
        "course.\n\n"
        "The run command is shorthand: given `playgrounds.counter`, pysx looks for a callable "
        "named `app` inside that module. Written in full it is "
        "`pysx --app playgrounds.counter:app`. The part before the colon is an importable "
        "module; the part after it is the exported callable or host, and its name is yours "
        "to choose. A project called `foo` whose entry point is `bar` runs as "
        "`pysx --app foo:bar`.\n\n"
        "The initial page is already meaningful HTML. A later input event travels to "
        "Python and the resulting small patch returns to this document. If you started the "
        "counter above, keep its terminal open; Ctrl+C shuts that server down along with "
        "the work it owns.",
        dedent('''
            import platform
            from importlib.metadata import version
            from pysx import Fragment, derived, pysx, signal


            def app() -> Fragment:
                name = signal("Learner")
                message = derived(lambda: f"Hello, {name()}!")
                python_version = platform.python_version()
                pysx_version = version("pysx")
                environment = f"Python {python_version} · pysx {pysx_version} · server process"

                return pysx(t"""
                    label(for="greeting-name"): "Your name"
                    input(id="greeting-name" bindValue={name} maxLength={80})
                    output(id="greeting-output"): {message}
                    p(id="environment"): {environment}
                """)
        ''').strip(),
        "`name` starts as Learner. The input writes that signal and `message` derives the "
        "greeting from it. Both live inside `app`, so a new tab starts its own greeting.",
        "Change the name in the live greeting. Then open the same lesson in another tab "
        "and compare its initial name. Use Reset to repeat the experiment.",
        "The greeting follows your input immediately. Another tab starts with Learner; "
        "its private form does not inherit your edit.",
        "The server owns application Python. The browser displays HTML and delivers events.",
        ("hello", "counter"),
        "pip install pysx\n"
        "pysx-playground counter\n\n"
        "# pysx-playground writes the file; this runs it\n"
        "pysx --app playgrounds.counter\n",
    ),
    Lesson(
        2,
        "templates-and-elements",
        "Write readable templates",
        "Foundations",
        "Build semantic markup and distinguish text, attributes and live holes.",
        "The template language uses indentation to express ownership. A heading belongs "
        "inside its section because it is indented beneath that section, just as a Python "
        "statement belongs inside a block. Native HTML remains native HTML: labels, "
        "buttons and inputs keep the browser's accessibility and validation behavior.\n\n"
        "A quoted string in markup is literal text. A hole inserts a Python value. These "
        "are separate operations, so a name containing `<strong>` is displayed as data "
        "rather than turning into an element or a script.",
        "A colon introduces content. Elements with attributes use parentheses, and a "
        "childless `input` needs no content body. Attribute names are checked by advisory "
        "tooling; the actual browser behavior still deserves a runtime test.\n\n"
        "In the card builder, a text field supplies an ordinary string while the emphasis "
        "control changes an attribute. Inspect the visible preview and the generated text. "
        "Changing data should not require constructing new Python source. Never evaluate "
        "a learner's string as a template.",
        dedent('''
            from pysx import Fragment, derived, pysx, signal


            def app() -> Fragment:
                text = signal("A readable interface")
                strong = signal(False)
                weight = derived(lambda: "font-weight: 800" if strong() else "font-weight: 400")

                def toggle(_: object) -> None:
                    strong.set(not strong())

                return pysx(t"""
                    label(for="card-text"): "Card text (ordinary escaped data)"
                    div:
                        input(id="card-text" bindValue={text} maxLength={200})
                        button(id="card-emphasis" onClick={toggle} ariaPressed={strong}):
                            "Toggle emphasis"
                    section:
                        p(id="card-preview" style={weight}): {text}
                """)
        ''').strip(),
        "`text` starts as A readable interface. The input binds it directly; the preview inserts "
        "it as escaped data. Toggle emphasis flips `strong`, which changes the derived font "
        "weight between 400 and 800. The course also displays the corresponding markup below the "
        "preview.",
        "Enter `<em>Hello</em>` as the card's text, then toggle emphasis and reset the card.",
        "Angle brackets stay visible as text. Emphasis changes the preview's attribute; "
        "it does not reinterpret your input as HTML.",
        "Use markup for structure and holes for data. Preserve native semantics.",
        ("templates", "attributes"),
    ),
    Lesson(
        3,
        "live-signals",
        "Live signals and snapshots",
        "Foundations",
        "Predict which values update after a write and which values stay fixed.",
        "A `Signal` is a small piece of reactive state. Calling it reads the current value; "
        "`set` replaces that value and `update` computes a replacement from the current one. "
        "State created inside the application belongs to that session.\n\n"
        "The most useful distinction is between passing a readable object and reading "
        "it now. A live template hole can subscribe to a Signal. A plain integer obtained "
        "from `count()` is just an integer, even if it originally came from a `Signal`.",
        "Create the snapshot before returning the template. The two outputs start at "
        "the same value, so initial HTML cannot prove which is reactive. Only a later "
        "write exposes the difference. This is why the exercise asks you to interact.\n\n"
        "Keep derived work out of event handlers when it can be expressed as a readable "
        "value. Your handler should change the source; the renderer can then update every "
        "dependent location consistently. Reset is another ordinary write, not a reload.",
        dedent('''
            from pysx import Fragment, pysx, signal


            def app(prefix: str = "") -> Fragment:
                count = signal(0)
                snapshot = count()

                def increment(_: object) -> None:
                    count.update(lambda value: value + 1)

                return pysx(t"""
                    button(id={prefix + "increment"} onClick={increment}): "Increment"
                    output(id={prefix + "live-count"}): {count}
                    p: "Snapshot: "
                    output(id={prefix + "snapshot-count"}): {snapshot}
                """)
        ''').strip(),
        "`snapshot` stores the initial integer, while the `count` hole stays live. Increment "
        "writes only `count`, so two clicks produce live 2 and snapshot 0.",
        "Increment twice, read both outputs, and reset. Try the same steps in another tab.",
        "Live reaches 2 while snapshot remains 0. Reset returns live to 0 without navigation.",
        "An uncalled readable stays live; an eager read is a snapshot.",
        ("counter", "reactive_state"),
    ),
    Lesson(
        4,
        "derived-values",
        "Derived values and operators",
        "Foundations",
        "Express dependent values once and let their dependencies drive updates.",
        "A total, a filter result and a validation condition are usually consequences "
        "of source state. Storing each consequence independently creates extra work: every "
        "handler must remember to update them all. A derived value records the sources "
        "read by its callback and recomputes from those sources.\n\n"
        "Python permits reactive arithmetic and ordering results, but some protocols "
        "require primitives. Python's `and`, `or`, `not`, `in` and `len` cannot faithfully return "
        "a `Signal`. Use the named helpers or ordinary Python inside `derived`.",
        "The calculator below has `quantity` as a source and `total` as a consequence. Its "
        "threshold indicator reads the same source. A single write can update both while "
        "keeping them consistent. Batch groups synchronous writes; it is not a transaction "
        "rollback and must not span an await.\n\n"
        "Use `eq`/`ne` for reactive payload equality. `Signal` object identity remains useful "
        "to Python containers. A lambda passed to `derived` is also a good place for genuine "
        "Python short-circuiting, because only the sources actually read are tracked.",
        dedent('''
            from pysx import Fragment, batch, derived, pysx, signal


            def app() -> Fragment:
                quantity = signal(1)
                total = derived(lambda: quantity() * 12)
                threshold = quantity > 3

                def increase(_: object) -> None:
                    with batch():
                        quantity.update(lambda value: value + 1)

                return pysx(t"""
                    button(id="quantity-increase" onClick={increase}): "Add one item"
                    p: "Quantity: "
                    output(id="quantity"): {quantity}
                    p: "Total at 12 per item: "
                    output(id="total"): {total}
                    p: "Quantity exceeds three: "
                    output(id="threshold"): {threshold}
                """)
        ''').strip(),
        "`quantity` starts at 1. The `total` callback reads it and multiplies by 12; the reactive "
        "comparison `quantity > 3` drives the threshold. Add one item writes only the source. At "
        "quantity 4 the displays are 48 and True, without setting either dependent value.",
        "Increase quantity past three, observe the threshold, then reset. Explain why "
        "the total needs no separate `set` call.",
        "The total is quantity times 12, and the threshold changes when quantity exceeds three.",
        "Store sources; derive consequences. Choose helpers when Python forces a primitive.",
        ("operators", "reactive_state"),
    ),
    Lesson(
        5,
        "browser-events",
        "Handle browser events",
        "Foundations",
        "Read bounded keyboard data and choose immediate browser event policy.",
        "The server does not receive a live JavaScript Event object. It receives a "
        "typed snapshot containing supported fields. That boundary makes event data "
        "explicit and keeps a callback from acquiring arbitrary browser capabilities.\n\n"
        "Some decisions cannot wait for a round trip. Preventing a key's default action "
        "or stopping propagation must be declared when registering the handler, so the "
        "browser can act immediately. An eventual Python callback is too late to undo "
        "a default action that already occurred.",
        "`on_event` combines a callback with its local policy. The key filter limits which "
        "keys invoke that callback. Modifier fields let the server distinguish an ordinary "
        "press from Shift or Control combinations without guessing from text.\n\n"
        "Use semantic native buttons for activation. A clickable `div` needs keyboard "
        "and role behavior you would otherwise get for free. In the live exercise, the "
        "input retains focus while its event readout changes, so you can compare keys "
        "without clicking between every experiment.",
        dedent('''
            from pysx import BrowserEvent, Fragment, on_event, pysx, signal


            def app() -> Fragment:
                report = signal("No key yet")

                def key(event: BrowserEvent) -> None:
                    report.set(f"{event.key} · Shift: {event.shift} · Ctrl: {event.ctrl}")

                return pysx(t"""
                    label(for="event-input"): "Press a key here"
                    input(id="event-input" onKeydown={on_event(key)})
                    output(id="event-report" ariaLive="polite"): {report}
                """)
        ''').strip(),
        "`on_event` delivers a `BrowserEvent`. The handler records its actual key plus Shift and "
        "Ctrl flags, exactly as shown by the live readout. Reset demo clears the report by "
        "creating a new instance.",
        "Focus the event field. Press A, Shift+A and ArrowDown, then use Reset demo to "
        "clear the readout.",
        "The readout reports the actual key and modifier values delivered by your browser.",
        "Server callbacks receive snapshots; immediate cancellation belongs in local policy.",
        ("events",),
    ),
    Lesson(
        6,
        "forms-and-bindings",
        "Forms and native validation",
        "Foundations",
        "Bind fields, preserve browser validation and handle authoritative corrections.",
        "A form has two directions of information flow. User edits update server state, "
        "and server corrections update the control's property. Writing a `value` attribute "
        "alone is not enough once a user has made a field dirty. Bindings connect the "
        "control to a writable readable and coordinate the round trip.\n\n"
        "A `label` needs a matching field `id`. Required fields should keep native constraint "
        "validation: an invalid submit is rejected before the application accepts it. "
        "ARIA descriptions add context but do not replace a real label.",
        "`bindValue` handles text-like values and `bindChecked` handles boolean checked state. "
        "Select and radio controls have their own native property semantics. Programmatic "
        "correction must preserve useful caret state rather than replacing the whole input.\n\n"
        "The exercise separates changing a draft from submitting it. A native form "
        "submission gives the app a deliberate commit point. Reset should visibly restore "
        "the lesson's initial values, while a server correction demonstrates that the "
        "server remains authoritative over the bound value.",
        dedent('''
            from pysx import Fragment, pysx, signal


            def app() -> Fragment:
                name = signal("")
                accepted = signal(False)
                result = signal("Not submitted")

                def submit(_: object) -> None:
                    if accepted():
                        result.set(f"Accepted {name()}")
                    else:
                        result.set("Accept the terms before submitting")

                def correct(_: object) -> None:
                    name.set("Server correction")

                return pysx(t"""
                    form(id="lesson-form" onSubmit={submit})
                        label(for="form-name"): "Name (required)"
                        input(id="form-name" bindValue={name} required={True})
                        label(for="form-terms" class="check-field")
                            input(id="form-terms" type="checkbox" bindChecked={accepted})
                            "Accept the demonstration terms"
                        button(id="form-submit" type="submit"): "Submit"
                    div:
                        button(id="form-correct" onClick={correct}): "Apply server correction"
                    output(id="form-echo"): {name}
                    output(id="form-result" role="status"): {result}
                """)
        ''').strip(),
        "The name and checkbox bind to separate writable signals. Native `required` blocks an "
        "empty name; submit checks acceptance and reports the name. Apply server correction "
        "writes the same name signal that controls the field and preview.",
        "Try submitting an empty name. Enter a name, accept the terms, submit, then "
        "apply the server correction and reset.",
        "Empty input is rejected. A valid submit shows the accepted name; correction "
        "updates the actual field as well as its text preview.",
        "Keep semantic forms and labels, and use bindings for the complete property round trip.",
        ("forms", "attributes"),
    ),
    Lesson(
        7,
        "structured-state",
        "Structured state and projections",
        "Foundations",
        "Update a nested field without mutating a private snapshot or unrelated data.",
        "Real interfaces rarely consist only of counters. A profile contains nested "
        "fields, and a list contains records. A structured Signal keeps one owned root "
        "while projections provide typed writable views into that root.\n\n"
        "Calling the structured root returns a private snapshot. Mutating that copy "
        "must not silently change live state. Without this boundary, an innocent-looking "
        "dictionary edit could bypass notification, validation and session ownership.",
        "Indexing a supported structured path creates a projection rather than freezing "
        "the current payload. Write through the projection or replace/update the root. "
        "For dataclasses, `project` accepts explicit getter/setter functions and can keep "
        "immutable replacement logic in one place.\n\n"
        "In this lesson, editing the first score should not alter the second. The private "
        "copy experiment is intentionally visible: it changes the copy's readout while "
        "the live first-score readout stays put. This distinguishes real ownership from "
        "a demonstration that happens to print the expected initial value.",
        dedent('''
            from pysx import Fragment, pysx, signal


            def app() -> Fragment:
                profile = signal({"scores": {"first": 1, "second": 2}})
                first = profile["scores"]["first"]
                second = profile["scores"]["second"]
                copied = signal("No copy yet")

                def increment(_: object) -> None:
                    first.update(lambda value: value + 1)

                def copy(_: object) -> None:
                    snapshot = profile()
                    snapshot["scores"]["first"] = 99
                    copied.set(str(snapshot["scores"]["first"]))

                return pysx(t"""
                    div:
                        button(id="score-increment" onClick={increment}): "Increment first"
                        button(id="score-copy" onClick={copy}): "Change a private copy"
                    p: "First: "
                    output(id="score-first"): {first}
                    p: "Second: "
                    output(id="score-second"): {second}
                    p: "Private copy: "
                    output(id="score-copy-value"): {copied}
                """)
        ''').strip(),
        "`first` and `second` project into the same owned root. Increment writes through `first`; "
        "Change a private copy edits the detached dictionary returned by `profile()` and displays "
        "99 without writing the live scores.",
        "Increment the first score, then change a private copy. Compare first, second "
        "and the displayed copy before resetting.",
        "Only the first live score changes through its projection. The copy can show 99 "
        "without changing either live field.",
        "A projection writes through; a returned snapshot belongs to the caller.",
        ("reactive_state", "forms"),
    ),
)
