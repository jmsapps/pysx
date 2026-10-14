"""Real transport recovery, deterministic expiry and tab isolation."""

from itertools import count

from pysx import Application, Fragment, HostConfig, TransportLimits, native, signal

generations = count(1)


def app() -> Fragment:
    generation = next(generations)
    clicks = signal(0)
    draft = signal("server draft")

    return native.Main(
        native.P(str(generation), id="reconnect-generation"),
        native.P(clicks, id="reconnect-clicks"),
        native.Input(bind_value=draft, id="reconnect-field"),
        native.Button(
            "Increment",
            id="reconnect-increment",
            on_click=lambda _event: clicks.update(lambda value: value + 1),
        ),
    )


host = Application(app)
expired_host = Application(app, config=HostConfig(transport=TransportLimits(resume_ttl=0.05)))
