"""Initial and live output contexts reject injection and preserve private ownership."""

import asyncio
import re
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from pysx import Application, Dom, DomError, Fragment, TrustedHTML, each, native, render, signal
from pysx.hosting import request_from_headers
from pysx.output import attribute_name, attribute_value
from pysx.server import Session
from pysx.styles import css_text, variable_values

if TYPE_CHECKING:
    from pysx import DomRef


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        " JaVaScRiPt:alert(1)",
        "java\nscript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:evil",
        "file:///private",
        "\\\\evil.test",
    ],
)
def test_rendering_bootstrap_unsafe_urls_initial_and_live(url: str) -> None:
    with pytest.raises(ValueError, match="unsafe URL"):
        render(lambda: native.A("safe", href=url))
    value = signal("/safe")
    session = Session(lambda: native.A("safe", href=value))

    try:
        with (
            pytest.raises(ExceptionGroup, match="reactive transaction") as caught,
            session.context(),
        ):
            value.set(url)
        assert caught.value.subgroup(ValueError) is not None
        assert all(op.get("v") != url for op in session.pending)
    finally:
        session.dispose()


@pytest.mark.parametrize(
    "css",
    [
        "background:url(https://evil.test)",
        "width:expression(alert(1))",
        "color:red;}</style><script>evil</script>",
        r"background:u\72l(evil)",
        "background:u/**/rl(evil)",
        "@import 'evil'",
        "behavior:evil",
        "-moz-binding:evil",
        'background:image-set("https://evil.test" 1x)',
    ],
)
def test_rendering_bootstrap_dynamic_css_capabilities(css: str) -> None:
    with pytest.raises(ValueError, match=r"CSS|braces"):
        css_text(css)
    with pytest.raises(ValueError, match=r"CSS|property value"):
        variable_values({"private": css})
    with pytest.raises(ValueError, match="CSS"):
        render(lambda: native.Div("safe", style=css))


def test_rendering_bootstrap_names_executable_contexts_and_escaping() -> None:
    assert attribute_value("href", "/#fragment\\tail") == "/#fragment\\tail"

    for name in ['title" onclick="evil', "bad name", "<script>"]:
        with pytest.raises(ValueError, match="attribute name"):
            attribute_name(name)
    assert attribute_value("srcdoc", "<script>evil</script>") == "&lt;script&gt;evil&lt;/script&gt;"
    assert attribute_value("srcdoc", TrustedHTML("<p>trusted</p>")) == "<p>trusted</p>"
    hostile = '"</script><img src=x onerror=evil()> & 雪'
    rendered = render(
        lambda: native.custom_element("x-card", hostile, custom_attrs={"title": hostile})
    )
    assert "<img" not in rendered.body
    assert "&lt;/script&gt;" in rendered.body
    assert "&quot;" in rendered.body
    rendered.dispose()
    keyed = render(
        lambda: native.Div(each([hostile], lambda key: native.P(key), key=lambda key: key))
    )
    assert "<img" not in keyed.body
    keyed.dispose()


def test_rendering_bootstrap_nonce_private_response_and_session_isolation() -> None:
    async def exercise() -> None:
        def app() -> Fragment:
            return native.P("private", css=signal("color:red"))

        application = Application(app)
        first = await application.http(request_from_headers("/", [("host", "localhost:8750")]))
        second = await application.http(request_from_headers("/", [("host", "localhost:8750")]))
        assert first.status == second.status == 200
        nonces = [re.search(rb'nonce="([\w-]+)"', result.body) for result in (first, second)]
        assert all(nonce is not None for nonce in nonces)
        assert nonces[0] != nonces[1]
        headers = dict(first.headers)
        assert headers["cache-control"] == "private, no-store"
        assert "unsafe-eval" not in headers["content-security-policy"]
        assert "script-src 'self'" in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff"
        assert first.adoption is not None
        assert second.adoption is not None
        assert (
            application.runtime.pending[first.adoption].session.rendered.styles
            is not application.runtime.pending[second.adoption].session.rendered.styles
        )

        def redirect() -> Fragment:
            from pysx import current_request

            current_request().document.redirect = "javascript:evil()"

            return native.P("private")

        rejected = Application(redirect)
        assert (await rejected.http(request_from_headers("/", []))).status == 500
        assert not rejected.runtime.graphs
        await rejected.close()
        await application.close()

    asyncio.run(exercise())


def test_rendering_bootstrap_dom_capability_cannot_cross_sessions() -> None:
    async def exercise() -> None:
        refs: list[DomRef] = []

        def app() -> Fragment:
            ref = Dom().ref()
            refs.append(ref)

            return native.Div(ref=ref)

        first, second = Session(app), Session(app)
        node = replace(refs[1].handle(), controller=refs[0].controller)

        try:
            with pytest.raises(DomError, match=r"revoked|stale|mount|capability"):
                await node.focus()
        finally:
            first.dispose()
            second.dispose()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "declaration",
    [
        "grid-template-columns:repeat(3, minmax(0, 1fr))",
        "padding-bottom:env(safe-area-inset-bottom, 0px)",
        "color:light-dark(#111111, #eeeeee)",
        "width:round(up, 13px, 4px)",
        "clip-path:polygon(0 0, 100% 0, 50% 100%)",
        'content:"section > item"',
    ],
)
def test_rendering_bootstrap_dynamic_css_permits_standard_capabilities(declaration: str) -> None:
    assert css_text(declaration) == declaration
    assert attribute_value("style", declaration) == declaration
