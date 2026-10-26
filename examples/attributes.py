"""Native booleans, advisory attribute schemas and independently reactive keyed rows."""

from pysx import Fragment, derived, each, native, pysx, signal

from .components import Action, Card, Description, Page, Title, example_app, example_title


def app() -> Fragment:
    example_title("Attributes")
    enabled = signal(False)
    tone = signal("calm")
    rows = signal([1, 2])
    counts = {key: signal(0) for key in rows()}
    picked = signal("None")
    submits = signal(0)
    custom = native.Div(
        "Inspect the explicit custom attributes",
        id="attribute-raw",
        custom_attrs={"x-state": "ready", "x-tone": tone, "hx-get": "/api", "hx-swap": "outerHTML"},
    )

    def toggle(_: object) -> None:
        enabled.set(not enabled())

    def retone(_: object) -> None:
        tone.set("bright" if tone() == "calm" else "calm")

    def reverse(_: object) -> None:
        rows.set(list(reversed(rows())))

    def row(key: int) -> Fragment:
        count = counts[key]
        label = derived(lambda: f"Row {key}: {count()}")

        def increment(_: object) -> None:
            count.update(lambda value: value + 1)

        def choose(_: object) -> None:
            picked.set(label())

        return pysx(t"""
            div(data-row={key} data-n={count} data-tone={tone}
                class={derived(lambda: "changed" if count() else "original")})
                span(data-inner={count}): {label}
                Action(data-increment={key} onClick={increment}): "Increment row"
                Action(data-choose={key} disabled={enabled} onClick={choose}): "Choose row"
        """)

    def submit(_: object) -> None:
        submits.update(lambda value: value + 1)

    return pysx(t"""
        Page(id="attributes-example")
            Title: "Attributes you can inspect"
            Description:
                "Toggle native presence, inspect ARIA strings, then reorder independent rows."
            Card:
                Action(id="toggle-attrs" onClick={toggle} ariaExpanded={enabled}
                    ariaControls="attribute-details"): "Toggle attributes"
                label(htmlFor="attribute-field"): "A typed native field"
                input(id="attribute-field" type="text" placeholder="Type here" autoComplete="off"
                    maxLength={40} tabIndex={0} readOnly={enabled} required={enabled}
                    autoFocus={False})
                input(id="attribute-autofocus" type="text" autoFocus={True}
                    ariaLabel="Initially focused field")
                details(id="attribute-details" open={enabled})
                    summary: "Native details"
                    p: "Open follows the toggle."
                form(id="attribute-form" noValidate={False} onSubmit={submit})
                    label(for="required-field"): "Required field"
                    input(id="required-field" required={enabled})
                    Action(id="attribute-submit" type="submit"): "Submit"
                p(id="attribute-submits"): {submits}
                video(id="attribute-video" controls={enabled} autoPlay={False} loop={enabled}
                    muted={enabled} playsInline={enabled})
                input(id="attribute-check" type="checkbox" checked={enabled} disabled={enabled})
                div(id="attribute-panel" hidden={enabled} ariaHidden={enabled}): "Visible panel"
                my-widget(id="attribute-custom" dataTone={tone}
                    dataRevealDelay="200" ariaLabel="Custom attribute example")
                {custom}
            Card:
                Action(id="retone" onClick={retone}): "Change independent tone"
                Action(id="reverse-attrs" onClick={reverse}): "Reverse rows"
                {each(rows, row, key=lambda key: key)}
                p(id="attribute-picked"): {picked}
    """)


example = example_app(app)
