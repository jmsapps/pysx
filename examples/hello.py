"""A greeting built from shared components and native markup."""

from pysx import Fragment, pysx

from .components import Page, Title, example_app, example_title


def app() -> Fragment:
    example_title("Hello")

    return pysx(t"""
        Page(id="hello-example")
            Title: "Hello, world!"
    """)


example = example_app(app)
