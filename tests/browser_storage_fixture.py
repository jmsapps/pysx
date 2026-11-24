"""Live fixture for browser-local storage reached from Python without application scripts."""

from pysx import (
    BrowserEvent,
    Cookies,
    Fragment,
    StorageDecodeError,
    StorageError,
    StorageUnavailableError,
    Store,
    current_request,
    on_event,
    on_mount,
    own_task,
    pysx,
    signal,
)


def app() -> Fragment:
    store = Store()
    cookies = Cookies()
    seen = current_request().cookies.get("density", "none")
    value = signal("unset")
    failure = signal("")
    watched = signal("none")

    async def restore() -> None:
        stored = await store.get("theme")
        value.set("missing" if stored is None else stored)

    async def write(_event: BrowserEvent) -> None:
        await store.set("theme", "dark")
        value.set("dark")

    async def write_layout(_event: BrowserEvent) -> None:
        await store.set_json("layout", {"sidebar": "collapsed", "width": 240})
        value.set("layout-written")

    async def read_layout(_event: BrowserEvent) -> None:
        try:
            layout = await store.get_json("layout")
        except StorageDecodeError:
            value.set("decode-failed")

            return
        value.set("missing" if layout is None else str(layout))

    async def read_corrupt(_event: BrowserEvent) -> None:
        await store.set("corrupt", "{not json")

        try:
            await store.get_json("corrupt")
        except StorageDecodeError:
            raw = await store.get("corrupt")
            value.set(f"raw:{raw}")

    async def drop(_event: BrowserEvent) -> None:
        await store.remove("theme")
        value.set("removed")

    async def read_back(_event: BrowserEvent) -> None:
        stored = await store.get("theme")
        value.set("missing" if stored is None else stored)

    async def oversized(_event: BrowserEvent) -> None:
        try:
            await store.set("big", "x" * 9000)
        except StorageUnavailableError:
            failure.set("unavailable")
        except StorageError as error:
            failure.set(f"rejected:{type(error).__name__}")

    async def probe_unavailable(_event: BrowserEvent) -> None:
        try:
            await store.get("theme")
        except StorageUnavailableError:
            failure.set("unavailable")
        except StorageError as error:
            failure.set(f"error:{type(error).__name__}")

    async def write_cookie(_event: BrowserEvent) -> None:
        await cookies.set("density", "compact", max_age=600)
        value.set("cookie-written")

    async def read_cookie(_event: BrowserEvent) -> None:
        current = await cookies.get("density")
        value.set(f"cookie:{current}")

    async def drop_cookie(_event: BrowserEvent) -> None:
        await cookies.remove("density")
        value.set("cookie-removed")

    async def read_identity(_event: BrowserEvent) -> None:
        try:
            await cookies.get("pysx_browser")
        except StorageError as error:
            failure.set(f"reserved:{type(error).__name__}")

    store.watch("theme", lambda incoming: watched.set("removed" if incoming is None else incoming))
    on_mount(lambda: own_task("storage-restore", restore))

    return pysx(t"""
        main:
            p(id="value"):
                {value}
            p(id="failure"):
                {failure}
            p(id="watched"):
                {watched}
            button(id="write" onClick={on_event(write)}):
                "Write"
            button(id="write-layout" onClick={on_event(write_layout)}):
                "Write layout"
            button(id="read-layout" onClick={on_event(read_layout)}):
                "Read layout"
            button(id="read-corrupt" onClick={on_event(read_corrupt)}):
                "Read corrupt"
            button(id="drop" onClick={on_event(drop)}):
                "Remove"
            button(id="read-back" onClick={on_event(read_back)}):
                "Read back"
            button(id="oversized" onClick={on_event(oversized)}):
                "Oversized"
            button(id="probe" onClick={on_event(probe_unavailable)}):
                "Probe"
            p(id="server-cookie"):
                {seen}
            button(id="cookie-write" onClick={on_event(write_cookie)}):
                "Write cookie"
            button(id="cookie-read" onClick={on_event(read_cookie)}):
                "Read cookie"
            button(id="cookie-drop" onClick={on_event(drop_cookie)}):
                "Remove cookie"
            button(id="cookie-identity" onClick={on_event(read_identity)}):
                "Read identity cookie"
    """)
