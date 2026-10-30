"""Full course structure, executable lesson sources and real HTTP ownership."""

import ast
import asyncio
import html
import importlib
import sys
import urllib.request
from functools import partial
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from src.app import create_application
from src.catalog import LESSONS, neighbors
from src.components.code import CodeBlock
from src.components.pages import Inline
from src.demos import composition, foundations, navigation, operations, runtime
from src.durable import view as durable_view
from src.model import LabState
from src.themes import course_themes

from pysx import Fragment, RequestContext, Route, Router, signal
from pysx.compiler.loader import install_loader
from pysx.hosting.server import Session

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysx.runtime.wire import JSONValue

ROOT = Path(__file__).resolve().parents[1]


class CodeText(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.feed(source)

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


class WalkthroughControls(HTMLParser):
    """Compare teaching controls and values independently of course styling."""

    def __init__(self, source: str) -> None:
        super().__init__()
        self.controls: list[tuple[str, tuple[tuple[str, str | None], ...], str]] = []
        self.current: int | None = None
        self.feed(source)
        self.controls = [(tag, attrs, " ".join(text.split())) for tag, attrs, text in self.controls]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"input", "button", "output", "textarea"}:
            return
        relevant = tuple(
            sorted(
                (key, value)
                for key, value in attrs
                if key in {"id", "value", "checked", "required", "type"}
                or (key.startswith("data-") and not key.startswith("data-pysx"))
            )
        )
        self.controls.append((tag, relevant, ""))
        self.current = None if tag == "input" else len(self.controls) - 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"button", "output", "textarea"}:
            self.current = None

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            tag, attrs, text = self.controls[self.current]
            self.controls[self.current] = tag, attrs, text + data


def walkthrough_demo(number: int) -> Fragment:
    demos: dict[int, Callable[[], Fragment]] = {
        1: foundations.greeting,
        2: foundations.template_card,
        3: foundations.counter,
        4: foundations.calculator,
        5: foundations.events,
        6: foundations.forms,
        7: foundations.structured_state,
        8: composition.branches,
        9: composition.loops,
        10: composition.keyed_rows,
        11: composition.components,
        12: composition.effects,
        13: partial(navigation.styles, course_themes()),
        16: navigation.keyboard,
        17: runtime.async_resource,
        18: runtime.errors,
        19: partial(runtime.async_resource, server_rendering=True),
        20: runtime.sessions,
        21: partial(runtime.recovery, clock=True),
        22: runtime.recovery,
        23: operations.codec,
        24: operations.authentication,
        25: operations.diagnostics,
        26: operations.hosting,
        27: operations.capstone,
        28: operations.codec,
    }

    if number in (14, 15):
        slug = LESSONS[number - 1].slug
        router = Router([Route("/", lambda state: navigation.routes(router, slug, state))])

        return router.view()

    return demos[number]()


def test_tutorial_website_pure_multiline_authoring_and_displayed_source() -> None:
    sources = [path.read_text() for path in (ROOT / "tutorial/src").rglob("*.py")]
    sources.extend(lesson.code for lesson in LESSONS)

    for source in sources:
        for node in ast.walk(ast.parse(source)):
            assert not (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "native"
            )

            if isinstance(node, ast.TemplateStr):
                assert node.end_lineno is not None
                assert node.end_lineno > node.lineno

    snippets = [lesson.code for lesson in LESSONS]
    snippets.extend(foundations.CARD_SOURCES)
    snippets.extend(
        (
            operations.FROZEN_SOURCE,
            operations.FROZEN_SOURCE.replace("count()", "count"),
            operations.ATTRIBUTE_SOURCE,
            operations.CORRECT_SOURCE,
        )
    )

    for snippet in snippets:
        session = Session(partial(CodeBlock, source=snippet))

        try:
            body = session.rendered.body
            assert "".join(CodeText(body).parts) == snippet
            assert 'data-syntax="' in body
        finally:
            session.dispose()


def test_tutorial_website_unknown_code_falls_back_to_escaped_text() -> None:
    source = '<script>alert("source is data")</script>\n{braces} \\n\t& quotes: " \' '
    session = Session(lambda: CodeBlock(source=source))

    try:
        assert "<script>alert" not in session.rendered.body
        assert "&lt;script&gt;" in session.rendered.body
        assert "".join(CodeText(session.rendered.body).parts) == source
        assert 'data-syntax="' not in session.rendered.body
    finally:
        session.dispose()


def test_tutorial_website_code_switches_between_generated_and_unknown_source() -> None:
    source = signal(operations.FROZEN_SOURCE)
    session = Session(lambda: CodeBlock(source=source))

    try:
        assert 'data-syntax="keyword"' in session.rendered.body
        source.set("<unknown>\n{plain}")
        body = cast("str", session.transport.repair(session.rendered.css)["html"])
        assert "".join(CodeText(body).parts) == source()
        assert 'data-syntax="' not in body
        source.set(operations.CORRECT_SOURCE)
        body = cast("str", session.transport.repair(session.rendered.css)["html"])
        assert "".join(CodeText(body).parts) == source()
        assert 'data-syntax="keyword"' in body
    finally:
        session.dispose()


def test_tutorial_website_inline_code_spans_render_and_escape() -> None:
    text = "Run `pysx --app foo:bar` for <b>any</b> project."
    session = Session(lambda: Inline(text=text))

    try:
        body = session.rendered.body
        assert "<code" in body
        assert "</code>" in body
        assert ">pysx --app foo:bar<" in body.replace("</pysx-slot>", "<")
        assert "`" not in body
        assert "<b>any</b>" not in body
        assert "&lt;b&gt;any&lt;/b&gt;" in body
    finally:
        session.dispose()


def test_tutorial_website_every_lesson_closes_its_code_spans() -> None:
    prose = ("objective", "introduction", "explanation", "walkthrough", "exercise", "expected")

    for lesson in LESSONS:
        for name in (*prose, "recap"):
            value = cast("str", getattr(lesson, name))
            assert value.count("`") % 2 == 0, (lesson.number, name)
        assert "`" not in lesson.code
        assert "`" not in lesson.commands


def test_tutorial_website_complete_course_graph_and_grounding() -> None:
    assert len(LESSONS) == 28
    assert [lesson.number for lesson in LESSONS] == list(range(1, 29))
    assert len({lesson.slug for lesson in LESSONS}) == 28

    for lesson in LESSONS:
        assert len((lesson.introduction + lesson.explanation + lesson.walkthrough).split()) >= 120
        assert lesson.objective
        assert lesson.exercise
        assert lesson.expected
        assert lesson.recap
        assert "from pysx" in lesson.code
        assert lesson.examples

        for example in lesson.examples:
            assert (ROOT / "examples" / f"{example}.py").is_file()
        previous, following = neighbors(lesson)
        assert previous[0] == (LESSONS[lesson.number - 2].path if lesson.number > 1 else "/")
        assert following[0] == (LESSONS[lesson.number].path if lesson.number < 28 else "/review")
    assert not (ROOT / "examples/tutorial.py").exists()


@pytest.mark.parametrize("path", ["/", "/review", *(lesson.path for lesson in LESSONS)])
def test_tutorial_website_every_page_http_ssr_and_cleanup(path: str, tmp_path: Path) -> None:
    async def exercise() -> None:
        host = create_application(tmp_path / "state")

        try:
            response = await host.http(RequestContext(path=path, headers={"host": "localhost"}))
            assert response.status == 200
            body = html.unescape(response.body.decode())
            assert "Previous:" in body
            assert "Next:" in body
            assert 'href="/assets/course.css"' in body

            if path.startswith("/learn/"):
                lesson = next(lesson for lesson in LESSONS if lesson.path == path)
                assert lesson.title in body
                assert 'id="reset-demo"' in body
                assert 'id="lesson-exercise"' in body
                assert body.count('data-previous="') == 2
                assert body.count('data-next="') == 2
            assert response.adoption is not None
        finally:
            await host.close()
        assert not host.runtime.graphs
        assert not host.lab.runtime.graphs

    asyncio.run(exercise())


@pytest.mark.parametrize("number", range(1, 29))
def test_tutorial_website_every_code_walkthrough_executes(number: int, tmp_path: Path) -> None:
    lesson = LESSONS[number - 1]
    name = f"course_snippet_{number}"
    (tmp_path / f"{name}.py").write_text(lesson.code)
    install_loader(roots=(tmp_path,))
    sys.path.insert(0, str(tmp_path))

    async def exercise() -> None:
        module = importlib.import_module(name)
        view = getattr(module, "app", None)

        if view is not None:
            session = Session(view)

            try:
                assert session.rendered.body
                reference = Session(lambda: walkthrough_demo(number))

                try:
                    assert (
                        WalkthroughControls(session.rendered.body).controls
                        == WalkthroughControls(reference.rendered.body).controls
                    )

                    if number == 4:
                        for quantity in (2, 3, 4):
                            changes = session.dispatch(next(iter(session.rendered.handlers)), None)
                            expected = reference.dispatch(
                                next(iter(reference.rendered.handlers)), None
                            )
                            values = [op["v"] for op in changes if op["op"] == "text"]
                            assert values == [op["v"] for op in expected if op["op"] == "text"]
                            assert str(quantity) in values
                            assert str(quantity * 12) in values

                            if quantity == 4:
                                assert "True" in values
                                assert "False" in values
                finally:
                    reference.dispose()
            finally:
                session.dispose()
                await asyncio.sleep(0)

        if number in (23, 28):
            declared = Session(lambda: module.view(signal(module.LabState())))
            reference = Session(lambda: durable_view(signal(LabState())))

            try:
                actual_controls = WalkthroughControls(declared.rendered.body).controls
                expected_controls = WalkthroughControls(reference.rendered.body).controls

                if number == 23:
                    ids = {"lab-draft", "lab-echo", "lab-count", "lab-setup"}
                    actual_controls = [
                        control for control in actual_controls if dict(control[1]).get("id") in ids
                    ]
                    expected_controls = [
                        control
                        for control in expected_controls
                        if dict(control[1]).get("id") in ids
                    ]

                def without_setup(
                    controls: list[tuple[str, tuple[tuple[str, str | None], ...], str]],
                ) -> list[tuple[str, tuple[tuple[str, str | None], ...], str]]:
                    return [
                        (tag, attrs, "" if dict(attrs).get("id") == "lab-setup" else text)
                        for tag, attrs, text in controls
                    ]

                assert without_setup(actual_controls) == without_setup(expected_controls)
                assert module.decode({"draft": "Saved draft", "count": 1}).count == 1

                with pytest.raises(ValueError, match="invalid declared lab state"):
                    module.decode({"draft": 42, "count": "invalid"})
            finally:
                declared.dispose()
                reference.dispose()
                await asyncio.sleep(0)

    try:
        asyncio.run(exercise())
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop(name, None)


def test_tutorial_website_unknown_route_and_real_login_cookie(tmp_path: Path) -> None:
    async def exercise() -> None:
        host = create_application(tmp_path / "state")

        try:
            missing = await host.http(RequestContext(path="/does-not-exist"))
            assert missing.status == 404
            login = await host.http(RequestContext(path="/demo-login/writer"))
            assert login.status == 303
            assert sum(name == "set-cookie" for name, _ in login.headers) == 2
            assert any("HttpOnly" in value for name, value in login.headers if name == "set-cookie")
            lab = await host.http(RequestContext(path="/labs/durable"))
            assert lab.status == 200
            assert any(
                "frame-ancestors 'self'" in value
                for name, value in lab.headers
                if name == "content-security-policy"
            )
            assert any(
                "frame-ancestors 'none'" in value
                for name, value in missing.headers
                if name == "content-security-policy"
            )
        finally:
            await host.close()

    asyncio.run(exercise())


@pytest.mark.acceptance
def test_tutorial_website_outside_checkout_installed_entry_and_all_routes(tmp_path: Path) -> None:
    import subprocess

    from browser_tutorial_fixture import TutorialWorker
    from prototypes.distribution import clean_env

    worker = TutorialWorker(tmp_path)
    probe = subprocess.run(
        [
            str(worker.python),
            "-I",
            "-c",
            "import pathlib,sys,src,pysx; "
            "assert pathlib.Path(src.__file__).is_relative_to(pathlib.Path(sys.argv[1])); "
            "assert pathlib.Path(pysx.__file__).is_relative_to(pathlib.Path(sys.prefix)); "
            "assert not any(p == sys.argv[2] or p.startswith(sys.argv[2] + '/') for p in sys.path)",
            str(worker.site),
            str(ROOT),
        ],
        cwd=worker.site,
        env=clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    worker.start()

    try:
        for path in ("/", "/review", *(lesson.path for lesson in LESSONS)):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{worker.port}{path}", timeout=10
            ) as response:
                assert response.status == 200
                assert b"Previous:" in response.read()
    finally:
        worker.stop()


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"draft": 42, "count": 0},
        {"draft": "", "count": True},
        {"draft": "", "count": -1},
        {"draft": "x" * 513, "count": 0},
        {"draft": "", "count": 0, "tasks": "not a list"},
        {"draft": "", "count": 0, "tasks": [{"id": 1, "title": "x", "done": "false", "note": ""}]},
    ],
)
def test_tutorial_website_declared_codec_rejects_invalid_state(value: object) -> None:
    from src.durable import decode

    with pytest.raises(ValueError, match=r"invalid|expected"):
        decode(cast("JSONValue", value))
