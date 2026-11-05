"""Course-owned visual tokens and session-local theme selection."""

from pysx import Themes


def course_themes() -> Themes:
    themes = Themes()
    themes.register(
        "light",
        {
            "paper": "#f7f9fe",
            "surface": "#ffffff",
            "ink": "#102449",
            "muted": "#526584",
            "line": "#d6deed",
            "accent": "#155ed9",
            "detail": "#6835c9",
            "wash": "#eaf1ff",
            "code": "#091a37",
            "code-ink": "#e4edff",
        },
    )
    themes.register(
        "dark",
        {
            "paper": "#09162d",
            "surface": "#10213d",
            "ink": "#e8efff",
            "muted": "#a9bbd6",
            "line": "#2b4166",
            "accent": "#54cdff",
            "detail": "#bd9aff",
            "wash": "#14294b",
            "code": "#071329",
            "code-ink": "#e4edff",
        },
    )
    themes.select("light")

    return themes


def selected_name(themes: Themes) -> str:
    current = themes.current()

    return current.name if current is not None else "light"
