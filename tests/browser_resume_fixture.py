"""Current-state repair with retained inputs and keyed rows."""

from pysx import Fragment, each, native, own_timer, signal


def app() -> Fragment:
    rows = signal([1, 2, 3])
    label = signal("seed")
    draft = signal("keep this selection")

    def update() -> None:
        rows.set([3, 4, 1])
        label.set("current")

    own_timer("update", 1.5, update)

    return native.Main(
        native.Input(bind_value=draft, id="resume-field"),
        native.P("one setup", id="resume-setup"),
        each(
            rows,
            lambda key: native.P(label, id=f"resume-row-{key}", title=label),
            key=lambda key: key,
        ),
    )
