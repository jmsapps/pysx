"""Browser-local preferences read and written from Python, with no application JavaScript."""

from pysx import (
    BrowserEvent,
    Cookies,
    Fragment,
    StorageError,
    Store,
    Themes,
    current_request,
    on_event,
    on_mount,
    own_task,
    pysx,
    signal,
    styled,
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
THEMES = ("light", "dark")

PALETTES = {
    "light": {
        "canvas": "#f6f7fb",
        "surface": "#ffffff",
        "surface-soft": "#f8f9fc",
        "ink": "#192338",
        "muted": "#66738a",
        "border": "#e4e8f0",
        "accent": "#6554d9",
        "accent-hover": "#5342c3",
        "accent-soft": "#eeebfc",
        "danger": "#b54450",
    },
    "dark": {
        "canvas": "#111827",
        "surface": "#1f2937",
        "surface-soft": "#374151",
        "ink": "#f9fafb",
        "muted": "#d1d5db",
        "border": "#4b5563",
        "accent": "#8b5cf6",
        "accent-hover": "#7c3aed",
        "accent-soft": "#4c1d95",
        "danger": "#f87171",
    },
}

ThemePreview = styled.div(t"""
    background: var(--surface-soft, #f8f9fc);
    color: var(--ink, #192338);
    border: 1px solid var(--border, #e4e8f0);
    border-left: 6px solid var(--accent, #6554d9);
    padding: 16px;
    border-radius: 8px;
""")


def app() -> Fragment:
    example_title("Browser storage")

    store = Store()
    cookies = Cookies()
    themes = Themes()

    for name, palette in PALETTES.items():
        themes.register(name, palette)

    initial = current_request().cookies.get("theme", "light")
    chosen_theme = initial if initial in THEMES else "light"
    themes.select(chosen_theme)

    theme = signal(chosen_theme)
    served = current_request().cookies.get("density", "the server default")
    density = signal("comfortable")
    recent = signal("")
    status = signal(f"Rendered as {chosen_theme} from the cookie the request carried")

    async def restore() -> None:
        """Repair from the browser's copy once it attaches. The cookie already decided paint."""

        try:
            saved = await store.get("density")
            searches = await store.get_json("recent-searches")
            stored_theme = await store.get("theme")
        except StorageError as error:
            # A browser that refuses storage keeps whatever the cookie already chose.
            status.set(f"Storage is unavailable, keeping {theme()}: {error}")

            return

        if saved in DENSITIES:
            density.set(saved)
        found = searches if isinstance(searches, list) else []
        recent.set(", ".join(str(item) for item in found) if found else "none yet")

        if stored_theme in THEMES and stored_theme != theme():
            # The cookie was cleared or expired; the browser's copy outlived it.
            themes.select(stored_theme)
            theme.set(stored_theme)
            await cookies.set("theme", stored_theme, max_age=31536000)
            status.set(f"Restored {stored_theme} from this browser and renewed the cookie")

            return
        status.set("Restored from this browser" if saved else "No saved preference yet")

    async def switch_theme(_event: BrowserEvent) -> None:
        chosen = "dark" if theme() == "light" else "light"
        themes.select(chosen)
        theme.set(chosen)

        # Write both: the cookie so the next render paints correctly, the browser copy so
        # the choice survives the cookie expiring.
        await cookies.set("theme", chosen, max_age=31536000)

        try:
            await store.set("theme", chosen)
        except StorageError:
            status.set(f"Switched to {chosen}; the cookie holds it, this browser will not")

            return
        status.set(f"Switched to {chosen} and saved it in both places")

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
        await store.remove("theme")
        await cookies.remove("density")
        await cookies.remove("theme")
        density.set("comfortable")
        recent.set("none yet")
        status.set("Cleared from this browser")

    store.watch("density", lambda value: status.set(f"Another tab chose {value or 'nothing'}"))
    on_mount(lambda: own_task("restore-preferences", restore))

    return pysx(
        t"""
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
                p(id="storage-theme"):
                    "Theme: "
                    {theme}
                ThemePreview(id="storage-preview"):
                    "This block follows the theme the cookie chose."
                Actions:
                    Action(id="storage-theme-toggle" type="button"
                           onClick={on_event(switch_theme)}):
                        "Switch theme"
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
    """,
        themes=themes,
    )


example = example_app(app)
