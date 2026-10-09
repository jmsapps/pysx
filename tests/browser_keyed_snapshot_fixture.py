"""Retained live snapshots and imperative ownership during structural changes."""

from pysx import BrowserEvent, Dom, Fragment, each, on_event, pysx, signal


def app() -> Fragment:
    rows = signal([0])
    label = signal("seed")
    status = signal("ready")
    clicks = signal(0)
    dom = Dom()
    zone = dom.ref(imperative=True)

    def change(_value: object) -> None:
        label.set("live")

    def replace(_value: object) -> None:
        rows.set([1])

    def remount(_value: object) -> None:
        rows.set([2])

    async def create(_event: BrowserEvent) -> None:
        root = zone.handle()
        child = await root.create_element("button")
        await child.set_attribute("id", "owned-child")
        await child.append(await root.create_text("Owned content"))
        await root.append(child)
        await child.listen("click", on_event(lambda _event: clicks.update(lambda value: value + 1)))
        status.set("created")

    async def inspect(_event: BrowserEvent) -> None:
        assert await zone.handle().query("#owned-child") is not None
        status.set("retained")

    def row(mode: int) -> Fragment:
        nonlocal zone
        constant = bool(mode)

        if mode == 2:
            zone = dom.ref()
        value = "seed" if constant else label
        extra = pysx(t'p: "New sibling"') if constant else pysx(t"fragment()")

        return pysx(t"""
            section(id="snapshot-row"):
              span(id="snapshot-label", title={value}): {value}
              input(id="snapshot-field", value={value})
              div(id="snapshot-zone", ref={zone})
              {extra}
        """)

    rendered_rows = each(rows, row, key=lambda _item: "a")

    return pysx(t"""
        button(id="snapshot-live", onClick={change}): "Live value"
        button(id="snapshot-replace", onClick={replace}): "Rebind constant and insert"
        button(id="snapshot-create", onClick={on_event(create)}): "Create owned content"
        button(id="snapshot-inspect", onClick={on_event(inspect)}): "Inspect retained handle"
        button(id="snapshot-remount", onClick={remount}): "Change ref owner"
        p(id="snapshot-status"): {status}
        p(id="snapshot-clicks"): {clicks}
        {rendered_rows}
    """)
