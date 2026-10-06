"""Reusable combobox and roving-focus controls using public browser capabilities."""

from pysx import BrowserEvent, Dom, Fragment, derived, html, native, on_event, signal, styled

from .controls import Action, Actions
from .forms import Field, SecondaryAction

ColorSwatch = styled.span(t"""
    display: inline-block;
    width: 32px;
    height: 32px;
    flex-shrink: 0;
    border-radius: 8px;
    border: 1px solid var(--border);
""")


def event_controls() -> Fragment:
    dom = Dom()
    combo = dom.ref()
    refs = [dom.ref() for _ in range(3)]
    choices = ("Red", "Blue", "Green")
    colors = {"Red": "#ef4444", "Blue": "#3b82f6", "Green": "#22c55e"}
    active = signal(0)
    expanded = signal(False)
    selected = signal("")
    text = signal("")
    focused = signal("none")
    event_status = signal("Press a key in the color chooser")
    roving = signal(0)
    descendant = derived(lambda: f"option-{active()}")
    swatch_style = derived(lambda: f"background-color: {colors.get(selected(), '#e5e7eb')}")

    def keyboard(event: BrowserEvent) -> None:
        event_status.set(f"{event.key} · Shift: {event.shift} · Ctrl: {event.ctrl}")

        if event.key in {"ArrowDown", "ArrowUp"}:
            active.set((active() + (1 if event.key == "ArrowDown" else -1)) % 3)
            expanded.set(True)
        elif event.key == "Enter":
            selected.set(choices[active()])
            text.set(choices[active()])
            expanded.set(False)
        elif event.key == "Escape":
            expanded.set(False)

    async def choose(index: int, _event: BrowserEvent) -> None:
        selected.set(choices[index])
        text.set(choices[index])
        active.set(index)
        expanded.set(False)
        await combo.handle().focus()

    async def move(event: BrowserEvent) -> None:
        roving.set((roving() + (1 if event.key == "ArrowRight" else -1)) % 3)
        await refs[roving()].handle().focus()

    async def focus(_event: BrowserEvent) -> None:
        await combo.handle().focus()

    def option(index: int, label: str) -> Fragment:
        async def selected_option(event: BrowserEvent) -> None:
            await choose(index, event)

        return native.Button(
            label,
            id=f"option-{index}",
            type="button",
            role="option",
            class_name=SecondaryAction.css_class,
            tabindex=-1,
            custom_attrs={"aria-selected": derived(lambda: active() == index)},
            on_pointerdown=on_event(selected_option, prevent_default=True),
        )

    def chip(index: int, label: str) -> Fragment:
        def select_color(_event: BrowserEvent) -> None:
            selected.set(label)
            text.set(label)
            active.set(index)
            roving.set(index)
            expanded.set(False)

        return native.Button(
            label,
            id=f"chip-{index}",
            type="button",
            ref=refs[index],
            class_name=SecondaryAction.css_class,
            tabindex=derived(lambda: 0 if roving() == index else -1),
            on_keydown=on_event(move, keys=("ArrowRight", "ArrowLeft"), prevent_default=True),
            on_click=on_event(select_color),
            on_focus=on_event(lambda _event: focused.set(f"chip-{index}")),
            on_blur=on_event(lambda _event: focused.set("none")),
        )

    options = native.Fragment(*(option(index, label) for index, label in enumerate(choices)))
    chips = native.Fragment(*(chip(index, label) for index, label in enumerate(choices)))
    field = native.Input(
        id="combo",
        ref=combo,
        bind_value=text,
        role="combobox",
        class_name=Field.css_class,
        custom_attrs={
            "aria-controls": "options",
            "aria-expanded": expanded,
            "aria-activedescendant": descendant,
        },
        on_keydown=on_event(
            keyboard, keys=("ArrowDown", "ArrowUp", "Enter", "Escape"), prevent_default=True
        ),
    )

    focus_button = native.Button(
        "Focus color chooser",
        id="focus-combo",
        class_name=Action.css_class,
        on_click=on_event(focus),
    )
    after_button = native.Button(
        "After",
        id="after",
        type="button",
        tabindex=0,
        class_name=SecondaryAction.css_class,
    )
    selection = native.Div(
        native.Span(
            class_name=ColorSwatch.css_class, style=swatch_style, custom_attrs={"aria-hidden": True}
        ),
        native.Span("Selected color: "),
        native.Span(derived(lambda: selected() or "None selected"), id="selected"),
        class_name=Actions.css_class,
        role="status",
    )

    return html(t"""
        div(id="keyboard-fixture" style="display: grid; gap: 16px;")
            label(for="combo"): "Choose a color"
            {field}
            div(id="options" role="listbox" hidden={derived(lambda: not expanded())})
                div(class={Actions.css_class})
                    {options}
            {selection}
            p(id="key-event" role="status"): {event_status}
            div(role="group" ariaLabel="Color shortcuts" class={Actions.css_class})
                {chips}
            p(id="focused"): {focused}
            div(class={Actions.css_class})
                {after_button}
                {focus_button}
    """)
