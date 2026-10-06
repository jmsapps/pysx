"""Inherited styles, live CSS variables and independent named themes."""

from pysx import Fragment, Themes, derived, html, signal

from .components.controls import Actions, Description, Eyebrow, Title
from .components.themes import Preview, ThemeAction, ThemePage


def app() -> Fragment:
    themes = Themes()
    themes.register(
        "light",
        {
            "canvas": "#f6f7fb",
            "surface": "#ffffff",
            "surface-soft": "#f8f9fc",
            "ink": "#192338",
            "muted": "#66738a",
            "border": "#e4e8f0",
            "accent": "#6554d9",
            "accent-hover": "#5342c3",
            "accent-soft": "#eeebfc",
        },
    )
    themes.register(
        "dark",
        {
            "canvas": "#111827",
            "surface": "#1f2937",
            "surface-soft": "#374151",
            "ink": "#f9fafb",
            "muted": "#d1d5db",
            "border": "#4b5563",
            "accent": "#8b5cf6",
            "accent-hover": "#7c3aed",
            "accent-soft": "#4c1d95",
        },
    )
    themes.select("light")
    spacing = signal("24px")
    accent = signal("")
    classes = signal("preview")

    def current_name() -> str:
        theme = themes.current()

        return theme.name if theme is not None else "cleared"

    name = derived(current_name)

    def light(_event: object) -> None:
        themes.select("light")

    def dark(_event: object) -> None:
        themes.select("dark")

    def clear(_event: object) -> None:
        themes.clear()

    def compact(_event: object) -> None:
        spacing.set("12px")

    def roomy(_event: object) -> None:
        spacing.set("36px")

    def local(_event: object) -> None:
        accent.set("#0d9488")
        classes.set("preview is-local")

    def reset(_event: object) -> None:
        spacing.set("")
        accent.set("")
        classes.set("preview")

    return html(
        t"""
        ThemePage(id="themes-example"):
            Eyebrow: "Scoped styles"
            Title: "Themes & variables"
            Description: "Switch the theme, adjust the preview, then open another tab."
            Description: "Each connection keeps its own theme and CSS variables."
            Actions:
                ThemeAction(id="light" onClick={light}): "Light"
                ThemeAction(id="dark" onClick={dark}): "Dark"
                ThemeAction(id="clear-theme" onClick={clear}): "Clear theme"
            p(id="theme-name"): {name}
            Preview(id="preview" class={classes} styleVars={
            ({"preview-space": spacing, "preview-accent": accent})
        }):
                strong: "Inherited preview"
                p: "This card inherits its layout and border from two styled bases."
                p: "Variables change padding and the accent without replacing the card."
            Actions:
                ThemeAction(id="compact" onClick={compact}): "Compact"
                ThemeAction(id="roomy" onClick={roomy}): "Roomy"
                ThemeAction(id="local-accent" onClick={local}): "Local accent"
                ThemeAction(id="reset-vars" onClick={reset}): "Reset variables"
    """,
        themes=themes,
        namespace={
            "ThemePage": ThemePage,
            "Preview": Preview,
            "ThemeAction": ThemeAction,
            "Actions": Actions,
            "Eyebrow": Eyebrow,
            "Title": Title,
            "Description": Description,
        },
    )
