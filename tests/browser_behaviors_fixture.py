"""Live fixture for browser-local behaviour declared without application JavaScript."""

from pysx import (
    BrowserEvent,
    DisconnectFor,
    Dom,
    Fragment,
    Link,
    Reveal,
    Route,
    Router,
    browser,
    each,
    on_event,
    pysx,
    signal,
)

TALL = "height: 1200px; border: 1px solid #ccc"


def card(label: str) -> Fragment:
    return pysx(t"""
        div(class="card" id={f"card-{label}"} style={TALL}
            behaviors={Reveal(visible_class="is-visible")}):
            {label}
    """)


def app() -> Fragment:
    dom = Dom()
    panel = dom.ref()
    connection = browser.connection()
    status = connection.text(
        connected="Connected",
        reconnecting="Reconnecting",
        default="Connecting",
    )
    extra = signal(False)
    order = signal(["a", "b"])

    def add(_event: BrowserEvent) -> None:
        extra.set(True)

    def drop_card(_event: BrowserEvent) -> None:
        extra.set(False)

    def reorder(_event: BrowserEvent) -> None:
        order.set(list(reversed(order())))

    router = Router(
        [
            Route("/", lambda _state: pysx(t"""p(id="route"): "home" """)),
            Route("/next", lambda _state: pysx(t"""p(id="route"): "next" """)),
            Route("*", lambda _state: pysx(t"""p(id="route"): "other" """)),
        ],
        scroll_container=panel,
    )
    go_next = Link("next", router=router, href="/next#anchor", id="go-next")
    go_home = Link("home", router=router, href="/", id="go-home")

    return pysx(t"""
        div(id="behaviors-fixture")
            p(id="status" role="status" aria-live="polite"):
                {status}
            button(id="drop" disabled={connection.not_connected}
                   onClick={DisconnectFor(seconds=1)}):
                "Disconnect"
            button(id="add" onClick={on_event(add)}): "Add"
            button(id="remove" onClick={on_event(drop_card)}): "Remove"
            button(id="reorder" onClick={on_event(reorder)}): "Reorder"
            nav:
                {go_next}
                {go_home}
            main(id="panel" ref={panel} style="height: 200px; overflow-y: auto")
                {router.view()}
                div(style="height: 1500px"): "panel filler"
                p(id="anchor"): "anchor"
            div(id="cards")
                {each(order, lambda label: card(label), key=lambda label: label)}
            div(id="late")
                if {extra}:
                    {card("late")}
    """)
