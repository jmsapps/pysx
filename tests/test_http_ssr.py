"""HTTP-only content, bounded graph lifetime and handler-free adoption."""

import asyncio
import re
from typing import TYPE_CHECKING

import pytest

from pysx import (
    Fragment,
    Route,
    Router,
    current_request,
    native,
    on_cleanup,
    on_mount,
    on_setup,
    signal,
)
from pysx.hosting.http import HTTPRuntime, reconcile
from pysx.hosting.request import RequestContext
from pysx.runtime.forms import PayloadError

if TYPE_CHECKING:
    from pysx.hosting.http import HTTPResult


def handle(result: HTTPResult) -> str:
    found = re.search(rb'data-pysx-adoption="([\w-]+)"', result.body)
    assert found is not None

    return found[1].decode()


def test_meaningful_http_head_controls_setup_adoption_and_mount() -> None:
    async def exercise() -> None:
        counts = {"setup": 0, "cleanup": 0, "mount": 0}

        def increment(key: str) -> None:
            counts[key] += 1

        def app() -> Fragment:
            request = current_request()
            request.document.title = "</title><script>private</script>"
            request.document.description = '"<&'
            on_setup(lambda: increment("setup"))
            on_cleanup(lambda: increment("cleanup"))
            on_mount(lambda: increment("mount"))
            text = signal("original")
            choice = signal("b")
            selected = signal(["b"])

            return native.Main(
                native.P("Meaningful content"),
                native.Input(bind_value=text),
                native.P(text),
                native.Textarea(bind_value=text),
                native.Select(
                    native.Option("A", value="a"), native.Option("B", value="b"), bind_value=choice
                ),
                native.Select(
                    native.Option("A", value="a"),
                    native.Option("B", value="b"),
                    multiple=True,
                    bind_selected=selected,
                ),
            )

        runtime = HTTPRuntime(app)
        request = RequestContext(principal="alice")
        result = runtime.render(request, "browser")
        body = result.body.decode()
        assert result.status == 200
        assert "Meaningful content" in body
        assert "&lt;/title&gt;&lt;script&gt;" in body
        assert "<script>private" not in body
        assert body.count('<option value="b" selected>') == 2
        assert "private, no-store" in dict(result.headers)["cache-control"]
        assert counts == {"setup": 1, "cleanup": 0, "mount": 0}
        token = handle(result)
        graph = runtime.pending[token].session
        with pytest.raises(ValueError, match="mismatched"):
            runtime.claim(token, RequestContext(principal="bob"), "browser")
        with pytest.raises(ValueError, match="mismatched"):
            runtime.claim(token, request, "other-browser")
        session = runtime.claim(token, request, "browser")
        assert session is graph
        binding = next(iter(session.rendered.bindings))
        reconcile(session, [(binding, "edited", 1)])
        assert session.rendered.bindings[binding].signal() == "edited"
        assert any(op["op"] == "text" and op["v"] == "edited" for op in session.pending)
        assert not any(
            op["op"] == "attr" and op["id"] == session.rendered.bindings[binding].element
            for op in session.pending
        )
        session.rendered.scopes.acknowledge(session.rendered.scopes.pending_mounts())
        assert counts["mount"] == 1
        with pytest.raises(ValueError, match="unknown"):
            runtime.claim(token, request, "browser")
        session.dispose()
        session.dispose()
        runtime.close()
        assert counts == {"setup": 1, "cleanup": 1, "mount": 1}

    asyncio.run(exercise())


def test_meaningful_http_deep_link_status_and_redirect() -> None:
    async def exercise() -> None:
        def app() -> Fragment:
            router = Router([Route("/item/:id", lambda state: native.H1(state.params()["id"]))])

            if current_request().path == "/redirect":
                router.navigate("/item/target")

            return router.view()

        runtime = HTTPRuntime(app)

        try:
            deep = runtime.render(RequestContext(path="/item/file.txt?q=1"), "one")
            assert deep.status == 200
            assert b"file.txt" in deep.body
            missing = runtime.render(RequestContext(path="/missing"), "two")
            assert missing.status == 404
            redirect = runtime.render(RequestContext(path="/redirect"), "three")
            assert redirect.status == 303
            assert dict(redirect.headers)["location"] == "/item/target"
            assert len(runtime.pending) == 2
        finally:
            runtime.close()

    asyncio.run(exercise())


def test_meaningful_http_principal_content_and_cleanup_are_isolated() -> None:
    async def exercise() -> None:
        cleanups: list[str | None] = []

        def app() -> Fragment:
            on_cleanup(lambda: cleanups.append(current_request().principal))

            return native.P(current_request().principal or "anonymous")

        runtime = HTTPRuntime(app)
        alice = runtime.render(RequestContext(principal="alice-private"), "alice-browser")
        bob = runtime.render(RequestContext(principal="bob-private"), "bob-browser")
        assert b"alice-private" in alice.body
        assert b"bob-private" not in alice.body
        assert b"bob-private" in bob.body
        assert b"alice-private" not in bob.body
        with pytest.raises(ValueError, match="mismatched"):
            runtime.claim(handle(alice), RequestContext(principal="bob-private"), "alice-browser")
        runtime.close()
        assert cleanups == ["alice-private", "bob-private"]

    asyncio.run(exercise())


def test_meaningful_http_expiry_capacity_failure_and_atomic_edits() -> None:
    async def exercise() -> None:
        closed: list[str] = []

        def app() -> Fragment:
            on_cleanup(lambda: closed.append(current_request().path))

            return native.Div(
                native.Input(bind_value=signal("first")), native.Input(bind_value=signal("second"))
            )

        runtime = HTTPRuntime(app, capacity=1, ttl=0.01)
        result = runtime.render(RequestContext(path="/closed"), "one")
        session = runtime.pending[handle(result)].session
        assert runtime.render(RequestContext(), "two").status == 503
        first, second = session.rendered.bindings
        with pytest.raises(PayloadError):
            reconcile(session, [(first, "changed", 1), (second, False, 1)])
        assert session.rendered.bindings[first].signal() == "first"
        with pytest.raises(PayloadError, match="unknown"):
            reconcile(session, [("unknown", "changed", 1)])
        await asyncio.sleep(0.03)
        assert runtime.pending == {}
        assert session.closed
        runtime.close()
        assert closed == ["/closed"]

        def broken() -> Fragment:
            on_cleanup(lambda: closed.append("broken"))

            raise RuntimeError("failed GET")

        failed = HTTPRuntime(broken)
        with pytest.raises(RuntimeError, match="failed GET"):
            failed.render(RequestContext(), "three")
        assert failed.pending == {}
        assert closed == ["/closed", "broken"]

    asyncio.run(exercise())
