"""Full course structure, executable lesson sources and real HTTP ownership."""

import ast
import asyncio
import html
import importlib
import sys
import tokenize
import urllib.request
from functools import partial
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from src.app import create_application
from src.catalog import LESSONS, neighbors
from src.components.code import CodeBlock
from src.components.code_sample import CodeSample
from src.components.pages import Inline
from src.demos import composition, foundations, navigation, operations, runtime
from src.themes import course_themes

from pysx import Fragment, RequestContext, Route, Router, Signal, pysx, signal
from pysx.compiler.loader import install_loader
from pysx.hosting.server import Session

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysx.runtime.wire import JSONValue

ROOT = Path(__file__).resolve().parents[1]
PLAYGROUND_LESSONS = {
    1: "counter",
    22: "recovery",
    23: "durable",
    25: "counter",
    28: "durable",
}


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


def displayed_samples() -> list[CodeSample]:
    return [
        *(lesson.sample for lesson in LESSONS),
        *foundations.CARD_SAMPLES,
        operations.FROZEN_SAMPLE,
        operations.LIVE_SAMPLE,
        operations.ATTRIBUTE_SAMPLE,
        operations.CORRECT_SAMPLE,
    ]


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

    for snippet in displayed_samples():
        session = Session(partial(CodeBlock, source=snippet))

        try:
            body = session.rendered.body
            assert "".join(CodeText(body).parts) == snippet.text
            assert 'data-syntax="' in body
        finally:
            session.dispose()


def test_tutorial_website_short_expressions_stay_readable() -> None:
    expected = """    if (
        not isinstance(draft, str)
        or len(draft) > 512
        or type(count) is not int
        or count < 0
    ):
        raise ValueError("invalid declared lab state")"""
    from src.snippets import operations as capstone_code
    from src.snippets import runtime as runtime_code

    assert expected in CodeSample(runtime_code.durable_state).text
    assert expected in CodeSample(capstone_code.capstone_resilience).text

    for sample in displayed_samples():
        source = sample.text

        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.Call, ast.Compare)) or node.lineno == node.end_lineno:
                continue

            # Keep multiline templates, lambdas, strings and explanatory comments intact.
            if any(
                isinstance(part, (ast.TemplateStr, ast.Lambda))
                or (
                    isinstance(part, ast.Constant)
                    and isinstance(part.value, str)
                    and "\n" in part.value
                )
                for part in ast.walk(node)
            ):
                continue
            segment = ast.get_source_segment(source, node)
            assert segment is not None

            if any(
                token.type == tokenize.COMMENT
                for token in tokenize.generate_tokens(StringIO(segment).readline)
            ):
                continue
            compact = ast.unparse(node)
            assert len(compact) + node.col_offset > 100, (sample.content.__name__, compact)


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


def test_tutorial_website_code_switches_between_authored_and_plain_source() -> None:
    source: Signal[CodeSample | str] = signal(operations.FROZEN_SAMPLE)
    session = Session(lambda: CodeBlock(source=source))

    try:
        assert 'data-syntax="keyword"' in session.rendered.body
        source.set("<unknown>\n{plain}")
        body = cast("str", session.transport.repair(session.rendered.css)["html"])
        assert "".join(CodeText(body).parts) == source()
        assert 'data-syntax="' not in body
        source.set(operations.CORRECT_SAMPLE)
        body = cast("str", session.transport.repair(session.rendered.css)["html"])
        assert "".join(CodeText(body).parts) == operations.CORRECT_SAMPLE.text
        assert 'data-syntax="keyword"' in body
    finally:
        session.dispose()


def test_tutorial_website_authored_colors_survive_source_formatting() -> None:
    def compact() -> Fragment:
        return pysx(rt"""
            "len(shown) "
            span(data-syntax="keyword"): "-"
            " "
            span(data-syntax="number"): "1"
        """)

    sample = CodeSample(compact)
    session = Session(lambda: CodeBlock(source=sample))

    try:
        assert sample.text == "len(shown) - 1"
        assert "".join(CodeText(session.rendered.body).parts) == sample.text
        assert 'data-syntax="keyword"' in session.rendered.body
        assert 'data-syntax="number"' in session.rendered.body
    finally:
        session.dispose()

    plain = Session(lambda: CodeBlock(source=sample.text))

    try:
        assert "".join(CodeText(plain.rendered.body).parts) == sample.text
        assert 'data-syntax="' not in plain.rendered.body
    finally:
        plain.dispose()


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
    assert {lesson.slug for lesson in LESSONS if lesson.commands} == {
        "getting-started",
        "browser-recovery",
        "durable-state",
        "development-workflow",
        "capstone-resilience",
    }

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


def test_tutorial_website_playground_walkthroughs_match_shipped_code() -> None:
    for number, name in PLAYGROUND_LESSONS.items():
        original = (ROOT / "pysx/playgrounds" / f"{name}.py").read_text()
        docstring = ast.parse(original).body[0]
        body = "".join(original.splitlines(keepends=True)[docstring.end_lineno :]).lstrip("\n")
        assert LESSONS[number - 1].code == body, name


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
            assert 'src="/assets/course.js"' in body

            if path == "/learn/reconnection":
                assert 'id="recovery-disconnect"' in body
                assert "Disconnect for 5 seconds" in body
                assert 'id="recovery-connection"' in body
            else:
                assert 'id="recovery-disconnect"' not in body

            if path.startswith("/learn/"):
                lesson = next(lesson for lesson in LESSONS if lesson.path == path)
                assert lesson.title in body
                assert ('id="reset-demo"' in body) is not bool(lesson.commands)
                assert 'id="lesson-exercise"' in body

                if lesson.commands:
                    assert "Try it locally" in body
                    assert "What you'll do" in body
                    assert "What you'll see" in body
                    assert "pip install" in body
                    assert "pysx-playground" in body
                    assert 'aria-label="Interactive lesson"' not in body
                    assert 'id="durable-frame"' not in body
                    assert 'id="codec-invalid"' not in body
                    assert '<ol id="lesson-exercise"' in body
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

        if number in PLAYGROUND_LESSONS:
            if PLAYGROUND_LESSONS[number] == "durable":
                try:
                    response = await module.app.http(
                        RequestContext(path="/", headers={"host": "localhost"})
                    )
                    assert response.status == 200
                    assert b"Durable declared state" in response.body
                    assert module.decode({"draft": "Saved", "count": 1}).count == 1
                    with pytest.raises(ValueError, match="invalid counter or draft"):
                        module.decode({"draft": 42, "count": "invalid"})
                finally:
                    await module.app.close()
            else:
                session = Session(module.app)

                try:
                    assert 'id="count"' in session.rendered.body or (
                        'id="history"' in session.rendered.body
                    )
                    payload = "Edited name" if PLAYGROUND_LESSONS[number] == "recovery" else None
                    changes = session.dispatch(next(iter(session.rendered.handlers)), payload)

                    if payload is None:
                        assert changes
                    else:
                        assert payload in str(
                            session.transport.repair(session.rendered.css)["html"]
                        )
                finally:
                    session.dispose()

            return
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
