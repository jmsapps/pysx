"""Local fallback and retry with a sibling that remains usable."""

from typing import TYPE_CHECKING

from pysx import ErrorBoundary, ErrorInfo, Fragment, local_state, on_cleanup, pysx, signal

from .components import (
    Action,
    Actions,
    Card,
    Description,
    Field,
    Page,
    Title,
    example_app,
    example_title,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def app() -> Fragment:
    example_title("Recovery")

    healthy = signal(0)
    draft = signal("Keep this text")
    attempts = signal(0)
    cleanups = signal(0)

    def content() -> Fragment:
        count = local_state("count", 0)
        on_cleanup(lambda: cleanups.update(lambda value: value + 1))

        def increment(_: object) -> None:
            count.update(lambda value: value + 1)

        def fail(_: object) -> None:
            attempts.update(lambda value: value + 1)

            raise RuntimeError("demonstration panel failure")

        return pysx(t"""
            Card(id="panel"):
                h2: "Recoverable panel"
                p(id="panel-count"): {count}
                Actions:
                    Action(id="increment-panel", onClick={increment}): "Increment panel"
                    Action(id="fail-panel", onClick={fail}): "Fail this panel"
        """)

    def fallback(info: ErrorInfo, retry: Callable[[], None]) -> Fragment:
        def reset(_: object) -> None:
            retry()

        return pysx(t"""
            Card(id="fallback", role="alert"):
                h2: "The panel needs a fresh start"
                p: {info.message}
                Action(id="retry", onClick={reset}): "Retry panel"
        """)

    boundary = ErrorBoundary(content, fallback)

    def increment_healthy(_: object) -> None:
        healthy.update(lambda value: value + 1)

    return pysx(
        t"""
        Page:
            Title: "Recoverable UI"
            Description: "Fail a panel, use its sibling, then retry."
            Boundary:
            Card(id="healthy-panel"):
                h2: "Healthy sibling"
                Field(id="healthy-field", bindValue={draft})
                p(id="healthy-count"): {healthy}
                Action(id="increment-healthy", onClick={increment_healthy}): "Increment sibling"
            p:
                "Failed attempts: "
                span(id="attempts"): {attempts}
            p:
                "Disposed panels: "
                span(id="cleanups"): {cleanups}
    """,
        namespace={
            "Boundary": boundary,
            "Page": Page,
            "Title": Title,
            "Description": Description,
            "Field": Field,
        },
    )


example = example_app(app)
