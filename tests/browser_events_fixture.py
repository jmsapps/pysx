"""Live fixture for immediate policies and typed dispatch."""

from pysx import BrowserEvent, Fragment, html, native, on_event, signal


def app() -> Fragment:
    events = signal("")
    count = signal(0)
    bound_value = signal("")

    def record(event: BrowserEvent) -> None:
        count.update(lambda value: value + 1)
        events.set(f"{event.type}:{event.key}:{event.shift}:{event.target}")

    def log(label: str, event: BrowserEvent) -> None:
        events.set(events() + label + event.type + ";")

    key = on_event(record, keys=("ArrowDown", "Enter"), prevent_default=True)
    parent = on_event(lambda event: log("parent-", event))
    child = on_event(lambda event: log("child-", event), stop_propagation=True)
    custom = on_event(record, event_type="custom-ready")
    link = on_event(record, eligible_link=True, prevent_default=True)
    capture = on_event(lambda event: log("capture-", event), phase="capture")
    bubble = on_event(lambda event: log("bubble-", event))
    reset_text = signal("initial")

    def reset_seen(_event: BrowserEvent) -> None:
        events.set(events() + f"reset:{reset_text()};")

    reset_form = native.Form(
        native.Input(id="reset-input", bind_value=reset_text),
        native.Button("Reset", id="reset", type="reset"),
        id="reset-form",
        on_reset=on_event(reset_seen),
    )
    bound = native.Input(
        id="bound",
        bind_value=bound_value,
        on_input=on_event(record, phase="capture", stop_propagation=True),
    )

    return html(t"""
        div(id="events-fixture")
            {native.Input(id="keys", on_keydown=key)}
            {bound}
            div(id="parent" onClick={parent})
                button(id="child" onClick={child}): "Child"
            div(onPointerDown={capture})
                button(id="pointer" onPointerDown={bubble}): "Pointer"
            div(id="custom" onCustom={custom}): "Custom"
            a(id="link" href="/internal" onClick={link}): "Link"
            a(id="hash-link" href="#native" onClick={link}): "Native hash"
            a(id="download-link" href="/download" download="file" onClick={link}): "Download"
            a(id="target-link" href="/other" target="_blank" onClick={link}): "New window"
            form(id="event-form" onSubmit={on_event(record)})
                button(id="submit" type="submit"): "Submit"
            {reset_form}
            p(id="reset-value"): {reset_text}
            p(id="event-result"): {events}
            p(id="event-count"): {count}
            p(id="bound-value"): {bound_value}
    """)
