"""Course-owned visual tokens and session-local theme selection."""

from pysx import Themes


def course_themes() -> Themes:
    themes = Themes()
    themes.register(
        "light",
        {
            "paper": "#faf8f3",
            "surface": "#ffffff",
            "ink": "#192a32",
            "muted": "#586b71",
            "line": "#d8dfdc",
            "accent": "#086d67",
            "wash": "#eaf3ee",
            "code": "#172b32",
            "code-ink": "#e2f3ed",
        },
    )
    themes.register(
        "dark",
        {
            "paper": "#12232a",
            "surface": "#192f37",
            "ink": "#ecf3ed",
            "muted": "#b1c4c6",
            "line": "#3a5359",
            "accent": "#7bd9bf",
            "wash": "#233e44",
            "code": "#0c1d23",
            "code-ink": "#e2f3ed",
        },
    )
    themes.select("light")

    return themes


def selected_name(themes: Themes) -> str:
    current = themes.current()

    return current.name if current is not None else "light"
