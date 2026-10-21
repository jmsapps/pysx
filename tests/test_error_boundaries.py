"""Local failure generation and ownership contracts."""

import asyncio
import re
from typing import TYPE_CHECKING

import pytest

from examples.recovery import app as recovery_app
from pysx import (
    ErrorBoundary,
    ErrorInfo,
    Fragment,
    derived,
    local_state,
    on_cleanup,
    on_mount,
    own_effect,
    pysx,
    signal,
)
from pysx.components.errors import BoundaryState
from pysx.hosting.server import Session
from pysx.rendering.tree import apply, snapshot

if TYPE_CHECKING:
    from collections.abc import Callable
    from string.templatelib import Template


def test_boundary_error_reset_and_stale_owner() -> None:
    state = BoundaryState()
    old = state.generation
    error = state.fail(old, "render")
    assert error is not None
    assert error.owner == old
    assert state.fail(old, "effect") is error
    assert state.reset(old)
    assert state.error is None
    assert state.generation != old
    assert state.fail(old, "async") is None
    assert not state.reset(old)
    state.close()
    assert state.fail(state.generation, "handler") is None
    assert not state.reset(state.generation)


@pytest.mark.parametrize(
    "phase", ["render", "computed", "effect", "handler", "async", "mount", "cleanup"]
)
def test_local_failure_contains_retries_and_preserves_sibling(phase: str) -> None:
    source = signal(1 if phase in ("render", "mount", "cleanup") else 0)
    healthy = signal(0)
    cleaned: list[str] = []

    def fail() -> None:
        if source() == 1 or phase in ("handler", "async"):
            raise ValueError("private traceback secret")

    def fallback(info: ErrorInfo, retry: Callable[[], None]) -> Template:
        assert "private" not in info.message

        def click_retry(_: object) -> None:
            retry()

        return t'\nbutton(id="retry", onClick={click_retry}): "Retry"'

    def child() -> Template:
        if phase == "render":
            fail()
        count = local_state("count", 0)
        on_cleanup(lambda: cleaned.append("child"))

        if phase == "cleanup":
            on_cleanup(fail)

        if phase == "mount":
            on_mount(fail)

        if phase == "effect":
            def observe() -> None:
                if source() == 1:
                    fail()

            own_effect("observe", observe)

        def compute() -> int:
            if phase == "computed" and source() == 1:
                fail()

            return source()

        value = derived(compute)

        def click(_: object) -> None:
            count.set(count() + 1)

            if phase == "handler":
                fail()
            source.set(1)

        async def async_click(_: object) -> None:
            count.set(count() + 1)
            await asyncio.sleep(0)
            fail()

        callback = async_click if phase == "async" else click

        return t'\nbutton(id="fail", onClick={callback}): {value}'

    visible = signal(True)

    def content() -> Fragment:
        return pysx(t"\nif {visible}:\n  Child:", namespace={"Child": child})

    def app() -> Fragment:
        def click_healthy(_: object) -> None:
            healthy.set(healthy() + 1)

        return pysx(
            t"""
            Boundary:
            button(id="healthy", onClick={click_healthy}): {healthy}
            """,
            namespace={"Boundary": boundary},
        )

    boundary = ErrorBoundary(content, fallback)
    session = Session(app)
    tree = snapshot(session.rendered.body, unwrap_row=False)
    sibling = re.search(r'id="healthy"[^>]*data-pysx-click="([^"]+)"', session.rendered.body)
    assert sibling is not None
    sibling_handler = sibling[1]

    try:
        if phase == "render":
            assert boundary.error is not None
        elif phase == "mount":
            session.rendered.scopes.acknowledge(session.rendered.scopes.pending_mounts())
            apply(tree, session.pending)
            session.pending.clear()
            session.rendered.failures.clear()
        elif phase == "cleanup":
            visible.set(False)
            apply(tree, session.pending)
            session.pending.clear()
            session.rendered.failures.clear()
        else:
            found = re.search(r'id="fail"[^>]*data-pysx-click="([^"]+)"', session.rendered.body)
            assert found is not None
            result = asyncio.run(session.dispatch_result_async(found[1], None))
            assert result["status"] == "error"
            assert "error" in result
            assert result["error"]["owner"] is not None
            assert "private" not in str(result)
            apply(tree, result["ops"])
            stale = asyncio.run(session.dispatch_result_async(found[1], None))
            assert stale["status"] == "stale"
        assert boundary.error is not None
        assert "Retry" in tree.markup()
        assert session.pending == []
        assert session.pending_callbacks == 0
        healthy_result = asyncio.run(session.dispatch_result_async(sibling_handler, None))
        assert healthy_result["status"] == "success"
        assert healthy() == 1
        assert boundary.error is not None
        source.set(0)
        visible.set(True)
        old = boundary.state.generation

        boundary.retry()
        assert boundary.error is None
        assert boundary.state.generation != old
        assert (
            len(
                [
                    owner
                    for owner in session.rendered.scopes.owners.values()
                    if "count" in owner.values
                ]
            )
            == 1
        )
    finally:
        session.dispose()
    assert source.observers == {}
    assert healthy.observers == {}
    assert session.rendered.handlers == {}


def test_local_failure_failing_fallback_escalates_to_parent() -> None:
    def fail() -> Template:
        raise ValueError("private")

    def failing_fallback(_info: ErrorInfo, _retry: Callable[[], None]) -> Template:
        return fail()

    def fallback(_info: ErrorInfo, _retry: Callable[[], None]) -> Template:
        return t'\nspan: "Parent recovered"'

    inner = ErrorBoundary(fail, failing_fallback)

    def content() -> Fragment:
        return pysx(t"\nInner:", namespace={"Inner": inner})

    outer = ErrorBoundary(content, fallback)
    session = Session(lambda: pysx(t"\nOuter:", namespace={"Outer": outer}))

    try:
        assert "Parent recovered" in session.rendered.body
        assert outer.error is not None
        assert not inner.state.alive
    finally:
        session.dispose()


def test_local_failure_owned_task_handoff_and_late_retry() -> None:
    from pysx import own_task

    retries: list[Callable[[], None]] = []

    async def exercise() -> None:
        async def task() -> object:
            await asyncio.sleep(0)

            raise ValueError("private")

        def content() -> Template:
            own_task("load", task)

            return t'\nspan: "Loading"'

        def fallback(_info: ErrorInfo, retry: Callable[[], None]) -> Template:
            retries.append(retry)

            return t'\nspan: "Retry"'

        boundary = ErrorBoundary(content, fallback)
        session = Session(lambda: pysx(t"\nBoundary:", namespace={"Boundary": boundary}))

        try:
            for _ in range(4):
                await asyncio.sleep(0)
            assert boundary.error is not None
            assert retries
        finally:
            session.dispose()
        generation = boundary.state.generation
        retries[0]()
        assert boundary.state.generation == generation

    asyncio.run(exercise())


def test_fallback_empty_example_retry_recreates_only_failed_owner() -> None:
    session = Session(recovery_app)
    tree = snapshot(session.rendered.body, unwrap_row=False)
    root = session.rendered.scopes.owners[""]

    def click(identifier: str) -> None:
        found = re.search(rf'id="{identifier}"[^>]*data-pysx-click="([^"]+)"', tree.markup())
        assert found is not None
        result = asyncio.run(session.dispatch_result_async(found[1], None))
        apply(tree, result["ops"])
        assert session.pending == []
        assert session.pending_callbacks == 0

    try:
        for _turn in range(1, 4):
            click("increment-panel")
            assert 'id="panel-count"><pysx-slot' in tree.markup()
            click("fail-panel")
            assert "The panel needs a fresh start" in tree.markup()
            click("increment-healthy")
            click("retry")
            assert 'id="panel"' in tree.markup()
            assert 'id="fallback"' not in tree.markup()
            assert session.rendered.scopes.owners[""] is root
            assert (
                len(
                    [
                        owner
                        for owner in session.rendered.scopes.owners.values()
                        if "count" in owner.values
                    ]
                )
                == 1
            )
            assert len(session.rendered.handlers) == 4
    finally:
        session.dispose()
    assert session.rendered.scopes.owners == {}
    assert session.rendered.handler_failures == {}


def test_local_failure_boundary_content_rebuilds_with_dom_and_owns_styles() -> None:
    from pysx import Dom, global_style, own_effect

    broken = signal(False)

    def content() -> Template:
        Dom()
        global_style(".boundary-owned { color: red }")

        def observe() -> None:
            if broken():
                raise ValueError("private")

        own_effect("observe", observe)

        return t'\nspan: "Content"'

    def fallback(_info: ErrorInfo, _retry: Callable[[], None]) -> Template:
        return t'\nspan: "Fallback"'

    boundary = ErrorBoundary(content, fallback)
    session = Session(lambda: pysx(t"\nBoundary:", namespace={"Boundary": boundary}))

    try:
        assert "boundary-owned" in session.rendered.styles.snapshot()
        broken.set(True)
        assert boundary.error is not None
        assert "boundary-owned" not in session.rendered.styles.snapshot()
        broken.set(False)
        boundary.retry()
        assert boundary.error is None
        assert "boundary-owned" in session.rendered.styles.snapshot()
    finally:
        session.dispose()


def test_local_failure_boundary_remounts_under_a_condition() -> None:
    visible = signal(True)

    def content() -> Template:
        return t'\nspan: "Content"'

    def fallback(_info: ErrorInfo, _retry: Callable[[], None]) -> Template:
        return t'\nspan: "Fallback"'

    boundary = ErrorBoundary(content, fallback)
    session = Session(
        lambda: pysx(t"\ndiv:\n  if {visible}:\n    Boundary:", namespace={"Boundary": boundary})
    )
    tree = snapshot(session.rendered.body, unwrap_row=False)

    try:
        for _ in range(2):
            visible.set(False)
            apply(tree, session.pending)
            session.pending.clear()
            assert "Content" not in tree.markup()
            visible.set(True)
            apply(tree, session.pending)
            session.pending.clear()
            assert "Content" in tree.markup()
        assert session.rendered.failures == []
        assert boundary.error is None
    finally:
        session.dispose()


def test_local_failure_owned_task_failing_after_unmount_reaches_the_loop() -> None:
    from pysx import own_task

    async def exercise() -> None:
        gate = asyncio.Event()

        async def task() -> object:
            await gate.wait()

            raise ValueError("private")

        def content() -> Template:
            own_task("load", task)

            return t'\nspan: "Loading"'

        def fallback(_info: ErrorInfo, _retry: Callable[[], None]) -> Template:
            return t'\nspan: "Fallback"'

        boundary = ErrorBoundary(content, fallback)
        session = Session(lambda: pysx(t"\nBoundary:", namespace={"Boundary": boundary}))
        reported: list[dict[str, object]] = []
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: reported.append(context))
        await asyncio.sleep(0)
        gate.set()
        loop.call_soon(session.dispose)

        for _ in range(6):
            await asyncio.sleep(0)
        assert boundary.error is None
        assert [context["message"] for context in reported] == ["owned task failed"]

    asyncio.run(exercise())
