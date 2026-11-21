"""Browser-local behaviour is declared in Python and validated before it reaches a client."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from pysx import BrowserBehaviorError, DisconnectFor, Reveal, browser
from pysx.runtime.browser import MAX_DISCONNECT_SECONDS, behavior_payload

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysx import Fragment


def test_browser_behaviors_reveal_serializes_its_declared_configuration() -> None:
    payload = Reveal(
        visible_class="is-visible", root_margin="0px 0px -10% 0px", threshold=0.25, once=True
    ).payload()
    assert payload == {
        "kind": "reveal",
        "visibleClass": "is-visible",
        "rootMargin": "0px 0px -10% 0px",
        "threshold": 0.25,
        "once": True,
    }


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"visible_class": ""}, "1 to 128"),
        ({"visible_class": "two names"}, "single class name"),
        ({"root_margin": "10"}, "px or % unit"),
        ({"root_margin": "widepx"}, "not a number"),
        ({"root_margin": "NaNpx"}, "finite CSS number"),
        ({"root_margin": "Infinitypx"}, "finite CSS number"),
        ({"root_margin": "1e309px"}, "finite CSS number"),
        ({"root_margin": "1_0px"}, "finite CSS number"),
        ({"root_margin": "1px\u00a02px"}, "CSS whitespace"),
        ({"root_margin": "0px 0px 0px 0px 0px"}, "one to four"),
        ({"threshold": 1.5}, "between 0 and 1"),
    ],
)
def test_browser_behaviors_reveal_rejects_unusable_configuration(
    arguments: dict[str, object], message: str
) -> None:
    """A descriptor that cannot be expressed to the client fails where it is written."""
    with pytest.raises(BrowserBehaviorError, match=message):
        Reveal(**arguments)  # type: ignore[arg-type]


def test_browser_behaviors_reveal_descriptors_are_frozen_and_shareable() -> None:
    """Configuration is shared; each attachment owns its own runtime state in the client."""
    shared = Reveal()

    with pytest.raises(AttributeError):
        shared.visible_class = "other"  # type: ignore[misc]
    assert shared == Reveal()
    assert hash(shared) == hash(Reveal())


@pytest.mark.parametrize("margin", ["+.5px -10%", "1e3px", "0px 0px -10% 0px"])
def test_browser_behaviors_reveal_accepts_finite_css_numbers(margin: str) -> None:
    assert Reveal(root_margin=margin).payload()["rootMargin"] == margin


def test_browser_behaviors_disconnect_action_bounds_its_duration() -> None:
    assert DisconnectFor(seconds=5).payload() == {"kind": "disconnect", "seconds": 5}

    for invalid in (0, -1, MAX_DISCONNECT_SECONDS + 1):
        with pytest.raises(BrowserBehaviorError, match="seconds must be"):
            DisconnectFor(seconds=invalid)


def test_browser_behaviors_connection_bindings_name_known_states() -> None:
    connection = browser.connection()
    text = connection.text(
        connected="Connected", reconnecting="Disconnected. Reconnecting…", default="Connecting…"
    )
    assert text.payload() == {
        "kind": "connection-text",
        "states": [["connected", "Connected"], ["reconnecting", "Disconnected. Reconnecting…"]],
        "default": "Connecting…",
    }
    assert connection.connected.payload() == {
        "kind": "connection-flag",
        "states": ["connected"],
        "negate": False,
    }
    assert connection.not_connected.payload()["negate"] is True

    with pytest.raises(BrowserBehaviorError, match="unknown connection state"):
        connection.text(default="…", offline="never")

    with pytest.raises(BrowserBehaviorError, match="unknown connection state"):
        connection.is_state("offline")

    with pytest.raises(BrowserBehaviorError, match="name at least one"):
        connection.is_state()


def test_browser_behaviors_connection_values_are_not_signals() -> None:
    """These stay live while the transport is down, so Python must not read them."""
    from pysx import Signal

    connection = browser.connection()
    assert not isinstance(connection.connected, Signal)
    assert not callable(connection.connected)


def test_browser_behaviors_attachment_rejects_ambiguous_combinations() -> None:
    """Two reveals on one element would fight over the same class."""
    assert behavior_payload(Reveal()) == [Reveal().payload()]
    assert len(behavior_payload([Reveal(visible_class="a")])) == 1

    with pytest.raises(BrowserBehaviorError, match="only once per element"):
        behavior_payload([Reveal(visible_class="a"), Reveal(visible_class="b")])

    with pytest.raises(BrowserBehaviorError, match="unsupported behavior"):
        behavior_payload("reveal")

    with pytest.raises(BrowserBehaviorError, match="unsupported behavior"):
        behavior_payload([DisconnectFor(seconds=1)])


def markup(component: object) -> str:
    from pysx import render

    return render(cast("Callable[[], Fragment]", component)).body


@pytest.mark.parametrize("use_styled", [False, True])
def test_browser_behaviors_reach_the_client_as_a_validated_element_payload(
    use_styled: bool,
) -> None:
    from pysx import pysx, section, styled

    card_tag = styled.section(t"color: red") if use_styled else section

    def app() -> object:
        return pysx(
            t"""
            Card(behaviors={Reveal(visible_class="shown", root_margin="0px 0px -10% 0px")}):
                "card"
        """,
            namespace={"Card": card_tag},
        )

    body = markup(app)
    assert "data-pysx-behavior=" in body
    assert "&quot;visibleClass&quot;:&quot;shown&quot;" in body
    assert "<script" not in body
    assert " behaviors=" not in body


def test_browser_behaviors_local_action_keeps_the_ordinary_handler_position() -> None:
    from pysx import pysx, render

    def app() -> object:
        return pysx(t"""button(onClick={DisconnectFor(seconds=5)}): "Drop" """)

    rendered = render(cast("Callable[[], Fragment]", app))
    assert 'data-pysx-local-click="{&quot;kind&quot;:&quot;disconnect&quot;' in rendered.body
    assert 'data-pysx-local-event="click"' in rendered.body
    assert not rendered.handlers, "a local action must not register a server handler"


def test_browser_behaviors_connection_bindings_render_an_offline_fallback() -> None:
    from pysx import pysx

    connection = browser.connection()

    def app() -> object:
        return pysx(t"""
            p:
                {connection.text(connected="Connected", default="Connecting")}
            button(disabled={connection.not_connected}): "Drop"
        """)

    body = markup(app)
    assert ">Connecting</pysx-local>" in body, "initial HTML must be readable without a client"
    assert "<button disabled " in body, "a control starts disabled until the client connects"
    assert "data-pysx-local-attrs=" in body


def test_browser_behaviors_disconnect_bound_matches_the_client_limit() -> None:
    from pathlib import Path

    client = Path(__file__).resolve().parents[1] / "pysx/static/client.js"
    assert f"duration > {MAX_DISCONNECT_SECONDS * 1000}" in client.read_text()


def test_browser_behaviors_navigation_scroll_container_is_nominated_by_ref() -> None:
    from pysx import Route, pysx, render
    from pysx.runtime.dom import Dom
    from pysx.runtime.routing import Router

    def app() -> object:
        panel = Dom().ref()
        routes = [Route(path="/", view=lambda _state: pysx(t"""p: "home" """))]
        Router(routes, scroll_container=panel)

        return pysx(t"""main(ref={panel}): "panel" """)

    assert 'data-pysx-scroll-container="true"' in render(cast("Callable[[], Fragment]", app)).body


def test_browser_behaviors_link_rejects_a_local_action_it_cannot_survive() -> None:
    from pysx import Route, pysx, render
    from pysx.runtime.routing import Link, Router

    def app() -> object:
        router = Router([Route(path="/", view=lambda _state: pysx(t"""p: "home" """))])

        return Link("home", router=router, href="/", on_click=DisconnectFor(seconds=1))

    with pytest.raises(TypeError, match="local action interrupts"):
        render(cast("Callable[[], Fragment]", app))
