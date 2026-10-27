"""Live demos for templates, source state, events and forms."""

import platform
from importlib.metadata import version

from pysx import BrowserEvent, Fragment, batch, derived, on_event, pysx, signal
from tutorial.components import Actions, Button, Field, Panel, Readout
from tutorial.components.code import CodeBlock

CARD_SOURCES = tuple(
    f'''pysx(t"""
    p(style="font-weight: {weight}"): {{text}}
""")'''
    for weight in ("400", "800")
)


def greeting() -> Fragment:
    name = signal("Learner")
    message = derived(lambda: f"Hello, {name()}!")
    environment = f"Python {platform.python_version()} · pysx {version('pysx')} · server process"

    return pysx(t"""
        label(for="greeting-name"): "Your name"
        Field(id="greeting-name" bindValue={name} maxLength={80})
        Readout(id="greeting-output"): {message}
        p(id="environment"): {environment}
    """)


def template_card() -> Fragment:
    text = signal("A readable interface")
    strong = signal(False)
    weight = derived(lambda: "font-weight: 800" if strong() else "font-weight: 400")
    source = derived(lambda: CARD_SOURCES[1 if strong() else 0])

    def toggle(_: object) -> None:
        strong.set(not strong())

    return pysx(t"""
        label(for="card-text"): "Card text (ordinary escaped data)"
        Field(id="card-text" bindValue={text} maxLength={200})
        Button(id="card-emphasis" onClick={toggle} ariaPressed={strong}): "Toggle emphasis"
        Panel:
            p(id="card-preview" style={weight}): {text}
        CodeBlock(identifier="card-source" source={source}):
    """)


def counter(prefix: str = "") -> Fragment:
    count = signal(0)
    snapshot = count()

    def increment(_: object) -> None:
        count.update(lambda value: value + 1)

    return pysx(t"""
        Button(id={prefix + "increment"} onClick={increment}): "Increment"
        Readout(id={prefix + "live-count"}): {count}
        p: "Snapshot: "
        Readout(id={prefix + "snapshot-count"}): {snapshot}
    """)


def calculator() -> Fragment:
    quantity = signal(1)
    total = derived(lambda: quantity() * 12)
    threshold = quantity > 3

    def increase(_: object) -> None:
        with batch():
            quantity.update(lambda value: value + 1)

    return pysx(t"""
        Button(id="quantity-increase" onClick={increase}): "Add one item"
        p: "Quantity: "
        Readout(id="quantity"): {quantity}
        p: "Total at 12 per item: "
        Readout(id="total"): {total}
        p: "Quantity exceeds three: "
        Readout(id="threshold"): {threshold}
    """)


def events() -> Fragment:
    report = signal("No key yet")

    def key(event: BrowserEvent) -> None:
        report.set(f"{event.key} · Shift: {event.shift} · Ctrl: {event.ctrl}")

    return pysx(t"""
        label(for="event-input"): "Press a key here"
        Field(id="event-input" onKeydown={on_event(key)})
        Readout(id="event-report" ariaLive="polite"): {report}
    """)


def forms() -> Fragment:
    name = signal("")
    accepted = signal(False)
    result = signal("Not submitted")

    def submit(_: object) -> None:
        result.set(f"Accepted {name()}" if accepted() else "Accept the terms before submitting")

    def correct(_: object) -> None:
        name.set("Server correction")

    return pysx(t"""
        form(id="lesson-form" onSubmit={submit})
            label(for="form-name"): "Name (required)"
            Field(id="form-name" bindValue={name} required={True})
            label(for="form-terms"): "Accept the demonstration terms"
            input(id="form-terms" type="checkbox" bindChecked={accepted})
            Button(id="form-submit" type="submit"): "Submit"
        Actions:
            Button(id="form-correct" onClick={correct}): "Apply server correction"
        Readout(id="form-echo"): {name}
        Readout(id="form-result" role="status"): {result}
    """)


def structured_state() -> Fragment:
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
        Actions:
            Button(id="score-increment" onClick={increment}): "Increment first"
            Button(id="score-copy" onClick={copy}): "Change a private copy"
        p: "First: "
        Readout(id="score-first"): {first}
        p: "Second: "
        Readout(id="score-second"): {second}
        p: "Private copy: "
        Readout(id="score-copy-value"): {copied}
    """)
