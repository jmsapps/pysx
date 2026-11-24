"""Browser-local preferences read and written from Python, with no application JavaScript."""

from pysx import (
    BrowserEvent,
    Cookies,
    Fragment,
    StorageError,
    Store,
    current_request,
    on_event,
    on_mount,
    own_task,
    pysx,
    signal,
)

from .components import (
    Action,
    Actions,
    Card,
    Description,
    Eyebrow,
    Page,
    Title,
    example_app,
    example_title,
)

DENSITIES = ("comfortable", "compact")


def app() -> Fragment:
    example_title("Browser storage")

    store = Store()
    cookies = Cookies()
    served = current_request().cookies.get("density", "the server default")
    density = signal("comfortable")
    recent = signal("")
    status = signal("Reading your saved preferences…")

    async def restore() -> None:
        try:
            saved = await store.get("density")
            searches = await store.get_json("recent-searches")
        except StorageError as error:
            status.set(f"Storage is unavailable: {error}")

            return

        if saved in DENSITIES:
            density.set(saved)
        found = searches if isinstance(searches, list) else []
        recent.set(", ".join(str(item) for item in found) if found else "none yet")
        status.set("Restored from this browser" if saved else "No saved preference yet")

    async def choose(_event: BrowserEvent) -> None:
        chosen = "compact" if density() == "comfortable" else "comfortable"
        density.set(chosen)

        try:
            await store.set("density", chosen)
        except StorageError as error:
            status.set(f"Could not save: {error}")

            return
        status.set("Saved to this browser")

    async def keep_for_server(_event: BrowserEvent) -> None:
        await cookies.set("density", density(), max_age=604800)
        status.set("Saved a cookie: reload to see the server read it")

    async def remember(_event: BrowserEvent) -> None:
        entries = ["signals", "templates", "routing"]
        await store.set_json("recent-searches", entries)
        recent.set(", ".join(entries))
        status.set("Saved a structured value")

    async def forget(_event: BrowserEvent) -> None:
        await store.remove("density")
        await store.remove("recent-searches")
        await cookies.remove("density")
        density.set("comfortable")
        recent.set("none yet")
        status.set("Cleared from this browser")

    store.watch("density", lambda value: status.set(f"Another tab chose {value or 'nothing'}"))
    on_mount(lambda: own_task("restore-preferences", restore))

    return pysx(t"""
        Page:
            header:
                Eyebrow: "pysx / examples"
                Title: "Browser storage"
                Description:
                    "Preferences live in this browser, not on the server. The application "
                    "reads and writes them from ordinary Python and ships no script of "
                    "its own. Open this page in a second tab to watch the two agree."
            Card:
                h2: "Saved preferences"
                p(id="storage-status" role="status" aria-live="polite"):
                    {status}
                p(id="storage-density"):
                    "Density: "
                    {density}
                p(id="storage-recent"):
                    "Recent searches: "
                    {recent}
                p(id="storage-served"):
                    "This page was rendered with: "
                    {served}
                Actions:
                    Action(id="storage-toggle" type="button" onClick={on_event(choose)}):
                        "Switch density"
                    Action(id="storage-remember" type="button" onClick={on_event(remember)}):
                        "Save recent searches"
                    Action(id="storage-cookie" type="button" onClick={on_event(keep_for_server)}):
                        "Tell the server too"
                    Action(id="storage-forget" type="button" onClick={on_event(forget)}):
                        "Clear this browser"
                Description:
                    "Values are read after the browser attaches, so the first render shows "
                    "the server's default. Tell the server too writes a cookie instead: "
                    "reload and the line above shows the server read it before rendering."
    """)


example = example_app(app)
