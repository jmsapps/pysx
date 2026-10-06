"""Setup corrections and mount acknowledgement for markup a failing event commits."""

from typing import TYPE_CHECKING

from pysx import Dom, Fragment, Signal, each, html, on_mount, on_setup, signal

if TYPE_CHECKING:
    from string.templatelib import Template


def app() -> Fragment:
    dom = Dom()
    unattached = dom.ref()
    rows: Signal[list[str]] = signal([])
    mounts = signal(0)
    status = signal("pending")

    def ready() -> Template:
        on_setup(lambda: status.set("ready"))

        return t'\nspan(id="ready"): "ok"'

    def row(key: str) -> Fragment:
        on_mount(lambda: mounts.set(mounts() + 1))

        return html(t"\nli(id={key}): {key}")

    async def reveal(_: object) -> None:
        rows.set(["a"])
        await unattached.handle().focus()

    return html(
        t"""
            main:
              p(id="status"): {status}
              p(id="mounts"): {mounts}
              Ready:
              button(id="reveal", onClick={reveal}): "Reveal"
              ul: {each(rows, row, key=str)}
        """,
        namespace={"Ready": ready},
    )
