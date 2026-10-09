"""Browser command fixture with reactive and imperative ownership boundaries."""

from pysx import BrowserEvent, Dom, DomError, DomNode, Fragment, on_event, pysx, signal


def app() -> Fragment:
    dom = Dom()
    field = dom.ref()
    zone = dom.ref(imperative=True)
    boundary = dom.ref()
    visible = signal(True)
    status = signal("ready")
    clicks = signal(0)
    saved: DomNode | None = None

    async def read(_event: BrowserEvent) -> None:
        nonlocal saved
        node = field.handle()
        saved = node
        await node.focus()
        await node.selection(1, 3)
        selection = await node.read_selection()
        value = await node.get_property("value")
        active = await node.active_element()
        rect = await node.measure()
        assert selection[:2] == (1, 3)
        assert selection[2] in {"none", "forward", "backward"}
        assert value == "abcdef"
        assert active is not None
        assert rect.width > 0
        assert await boundary.handle().get_by_id("outside") is None
        assert await boundary.handle().query("#missing") is None
        await node.blur()
        status.set("read-passed")

    async def create(_event: BrowserEvent) -> None:
        root = zone.handle()
        fragment = await root.create_fragment()
        child = await root.create_element("button")
        text = await root.create_text("Created")
        await child.append(text)
        await child.set_attribute("id", "created")
        await child.set_attribute("title", "owned")
        await child.set_property("disabled", False)
        await child.style("color", "red")
        await fragment.append(child)
        await root.append(fragment)
        assert await child.get_attribute("title") == "owned"
        assert await child.get_property("disabled") is False
        assert await root.query("#created") is not None
        await child.listen("click", on_event(lambda _event: clicks.update(lambda value: value + 1)))
        await child.listen(
            "custom-window",
            on_event(lambda _event: clicks.update(lambda value: value + 1)),
            window=True,
        )
        svg = await root.create_element("svg", namespace="svg")
        await root.insert(svg, child)
        await root.scroll(0, 0)
        status.set("created-passed")

    async def remove(_event: BrowserEvent) -> None:
        child = await zone.handle().query("#created")

        if child is not None:
            await child.remove()
        assert not dom.controller.listeners
        status.set("removed-passed")

    async def replace(_event: BrowserEvent) -> None:
        visible.set(not visible())

        if saved is not None:
            try:
                await saved.focus()
            except DomError:
                status.set("stale-passed")

                return
            status.set("stale-failed")

    async def forbidden(_event: BrowserEvent) -> None:
        try:
            await field.handle().set_property("value", "bad")
        except DomError:
            status.set("boundary-passed")

    async def mount(_event: BrowserEvent) -> None:
        visible.set(False)
        visible.set(True)
        await field.handle().focus()
        status.set("mount-passed")

    return pysx(t"""
        div(id="dom-fixture" ref={boundary})
            if {visible}:
                input(id="field" ref={field} value="abcdef")
            div(id="zone" ref={zone})
            button(id="read" onClick={on_event(read)}): "Read"
            button(id="create" onClick={on_event(create)}): "Create"
            button(id="remove" onClick={on_event(remove)}): "Remove"
            button(id="replace" onClick={on_event(replace)}): "Replace"
            button(id="forbidden" onClick={on_event(forbidden)}): "Forbidden"
            button(id="mount" onClick={on_event(mount)}): "Mount and focus"
            p(id="dom-status"): {status}
            p(id="dom-clicks"): {clicks}
        div(id="outside"): "Outside owner"
    """)
