"""Lifecycle controls for the three-engine acceptance suite."""

from pysx import Fragment, each, html, local_state, on_cleanup, on_mount, on_setup, signal


def app() -> Fragment:
    rows = signal(["a", "b"])
    setups = signal(0)
    cleanups = signal(0)
    mounts = signal(0)

    def row(key: str) -> Fragment:
        count = local_state("count", 0)
        on_setup(lambda: setups.set(setups() + 1))
        on_cleanup(lambda: cleanups.set(cleanups() + 1))
        on_mount(lambda: mounts.set(mounts() + 1))

        def increment(_: object) -> None:
            count.set(count() + 1)

        return html(t"""
            li(id={key}):
              button(onClick={increment}): {count}
        """)

    def reverse(_: object) -> None:
        rows.set(list(reversed(rows())))

    def remove(_: object) -> None:
        rows.set(["b"])

    def restore(_: object) -> None:
        rows.set(["a", "b"])

    return html(t"""
        main:
          ul: {each(rows, row, key=str)}
          p(id="setups"): {setups}
          p(id="cleanups"): {cleanups}
          p(id="mounts"): {mounts}
          button(id="reverse", onClick={reverse}): "Reverse"
          button(id="remove", onClick={remove}): "Remove A"
          button(id="restore", onClick={restore}): "Restore A"
    """)
