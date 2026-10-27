"""Full course structure, executable lesson sources and real HTTP ownership."""

import ast
import asyncio
import html
import importlib
import sys
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from src.app import create_application
from src.catalog import LESSONS, neighbors
from src.components.code import CodeBlock, highlighted_sources

from pysx import RequestContext
from pysx.compiler.loader import install_loader
from pysx.hosting.server import Session

if TYPE_CHECKING:
    from pysx.runtime.wire import JSONValue

ROOT = Path(__file__).resolve().parents[1]


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

    for lesson in LESSONS:
        parts = highlighted_sources()[lesson.code]
        assert "".join(part[1] for part in parts) == lesson.code
        assert any(part[0] != "plain" for part in parts)


def test_tutorial_website_highlighted_code_escapes_source(monkeypatch: pytest.MonkeyPatch) -> None:
    source = '<script>alert("source is data")</script>'
    monkeypatch.setitem(highlighted_sources(), source, [["string", source]])
    session = Session(lambda: CodeBlock(source=source))

    try:
        assert "<script>alert" not in session.rendered.body
        assert "&lt;script&gt;" in session.rendered.body
        assert 'data-syntax="string"' in session.rendered.body
    finally:
        session.dispose()


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
