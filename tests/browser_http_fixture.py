"""HTTP attachment observes controls edited while the external client is delayed."""

from pysx import Fragment, current_request, native, on_mount, on_setup, signal


def app() -> Fragment:
    setup = signal(0)
    mounted = signal(0)
    text = signal("initial")
    choice = signal("b")
    checked = signal(False)
    newline = signal("\nhello")
    implicit = signal("B")
    on_setup(lambda: setup.set(1))
    on_mount(lambda: mounted.set(1))
    current_request().document.title = "HTTP attachment"

    return native.Main(
        native.H1("HTTP content"),
        native.Input(id="text", bind_value=text),
        native.Textarea(id="note", bind_value=text),
        native.Input(id="check", type="checkbox", bind_checked=checked),
        native.Textarea(id="newline", bind_value=newline),
        native.Select(
            native.Option("A"),
            native.Optgroup(native.Option(signal("B")), label="group"),
            id="implicit",
            bind_value=implicit,
        ),
        native.Select(
            native.Option("A", value="a"),
            native.Option("B", value="b"),
            id="choice",
            bind_value=choice,
        ),
        native.P(text, id="text-state"),
        native.P(choice, id="choice-state"),
        native.P(checked, id="check-state"),
        native.P(setup, id="setup"),
        native.P(mounted, id="mounted"),
    )
