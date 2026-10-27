"""A counter: the smallest useful live application.

Scaffold this playground, then run it:

    pysx-playground counter
    pysx --app playgrounds.counter

Add --watch to the run command to edit this file and watch the page update.
"""

from pysx import Fragment, pysx, signal


def app() -> Fragment:
    count = signal(0)

    def increment(_event: object) -> None:
        count.set(count() + 1)

    return pysx(t"""
        main:
            h1: "Your first live application"
            p: "The button sends an event to Python, which sends back a small patch."
            p:
                "Count: "
                span(id="count"): {count}
            button(id="increment" type="button" onClick={increment}): "Increment"
            h2: "Try it"
            ol:
                li: "Increment the count a few times."
                li: "Refresh the page. The count returns to 0."
                li: "Open a second tab. Its count starts at 0 and is independent of this one."
    """)
