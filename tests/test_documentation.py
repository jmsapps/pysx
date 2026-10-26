"""Execute maintained lessons in checkout and isolated installed environments."""

import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from acceptance_support import stop_process
from prototypes.distribution import ROOT, clean_env, command, dependency_cache, installed
from test_dev_server import wait_for
from test_parity_corpus import CORPUS

import pysx

LESSON_FILES = ("docs/TUTORIAL.md", "docs/QUICKSTART.md")
SNIPPETS = [
    (f"{filename}:{index}", source)
    for filename in LESSON_FILES
    for index, source in enumerate(
        re.findall(r"```python\n(.*?)\n```", (ROOT / filename).read_text(), re.DOTALL)
    )
]
APP_CHECK = """
from pysx.hosting.server import Session
session = Session(app)
try:
    handler = next(iter(session.rendered.handlers))
    for expected in ("1", "2"):
        ops = session.dispatch(handler, None)
        assert any(op["op"] == "text" and op.get("v") == expected for op in ops)
finally:
    session.dispose()
"""


@pytest.fixture(scope="module")
def documentation_install(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    workspace = tmp_path_factory.mktemp("documentation-install")
    uv = shutil.which("uv")
    assert uv is not None
    cache = dependency_cache(workspace / "cache")
    artifacts = workspace / "artifacts"
    command(
        [
            uv,
            "--offline",
            "--cache-dir",
            str(cache),
            "build",
            str(ROOT),
            "--wheel",
            "--out-dir",
            str(artifacts),
            "--python",
            sys.executable,
        ],
        workspace,
    )
    python = installed(workspace, next(artifacts.glob("*.whl")), cache, extra=False)

    return python, workspace


@pytest.fixture(scope="module")
def documentation_consumer(documentation_install: tuple[Path, Path]) -> tuple[Path, Path]:
    python, workspace = documentation_install
    consumer = workspace / "consumer"
    consumer.mkdir()
    shutil.copytree(
        ROOT / "examples", consumer / "examples", ignore=shutil.ignore_patterns("__pycache__")
    )

    return python, consumer


def test_corpus_executable_public_api_and_contract_links() -> None:
    api = (ROOT / "docs/API.md").read_text()
    documented = set(re.findall(r"`([A-Za-z_]\w*)`", api))
    assert set(pysx.__all__) <= documented

    for name in pysx.__all__:
        assert getattr(pysx, name) is not None

    for filename in (*LESSON_FILES, "docs/API.md", "docs/MIGRATION.md"):
        path = ROOT / filename

        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if not target.startswith(("https:", "http:", "#")):
                assert (path.parent / target.split("#")[0]).is_file(), (filename, target)


@pytest.mark.parametrize(("identity", "source"), SNIPPETS, ids=[row[0] for row in SNIPPETS])
@pytest.mark.parametrize("environment", ["checkout", "installed"])
def test_corpus_executable_documented_snippets(
    identity: str,
    source: str,
    environment: str,
    documentation_install: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    assert identity
    python = Path(sys.executable) if environment == "checkout" else documentation_install[0]
    sample = tmp_path / "lesson.py"
    sample.write_text(source + (APP_CHECK if "def app(" in source else ""))
    command([str(python), str(sample)], tmp_path)


def test_corpus_executable_equivalents_from_fresh_artifact(
    documentation_consumer: tuple[Path, Path],
) -> None:
    python, consumer = documentation_consumer
    probe = consumer / "check_examples.py"
    probe.write_text("""
import asyncio
import importlib
import sys
from pathlib import Path
import pysx
from pysx import Application, HostConfig, RequestContext
from pysx.compiler.loader import install_loader

install_loader(packages=("examples",))

assert Path(pysx.__file__).is_relative_to(Path(__file__).parent.parent / "core-env")

async def check():
    for name in sys.argv[1:]:
        view = importlib.import_module(f"examples.{name}").app
        host = view if isinstance(view, Application) else Application(
            view, config=HostConfig(recovery=False, development=True)
        )
        try:
            response = await host.http(RequestContext(headers={"host": "localhost"}))
            assert response.status == 200, name
            assert b"pysx-root" in response.body, name
        finally:
            await host.close()
        assert not host.runtime.graphs, name

asyncio.run(check())
""")
    names = sorted({name for name, _ in CORPUS.values()})
    command([str(python), str(probe), *names], consumer)


@pytest.mark.acceptance
@pytest.mark.parametrize(
    ("name", "script"),
    sorted(
        set(CORPUS.values())
        | {
            ("todos", "browser_todo_edit.mjs"),
        }
    ),
)
def test_corpus_executable_installed_browser_interactions(
    name: str,
    script: str,
    documentation_consumer: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    python, consumer = documentation_consumer
    node = shutil.which("node")
    assert node is not None
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log = tmp_path / "installed-browser.log"
    environment = clean_env()
    environment.pop("DYLD_LIBRARY_PATH", None)
    args = [
        str(python),
        "-m",
        "pysx.hosting.server",
        "--app",
        f"examples.{name}:example",
        "--port",
        str(port),
    ]
    with log.open("w") as stream:
        server = subprocess.Popen(
            args,
            cwd=consumer,
            env=environment,
            stdout=stream,
            stderr=stream,
            text=True,
            start_new_session=sys.platform != "win32",
        )

        try:
            wait_for(lambda: "pysx ready" in log.read_text(), log)

            for engine in ("chromium", "firefox", "webkit"):
                result = subprocess.run(
                    [node, str(ROOT / "tests" / script), str(port)],
                    cwd=ROOT,
                    env={**environment, "PYSX_BROWSER_ENGINE": engine},
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=False,
                )
                assert result.returncode == 0, (
                    name,
                    engine,
                    result.stdout,
                    result.stderr,
                    log.read_text(),
                )
                assert "PASSED" in result.stdout
                assert "  ok  " in result.stdout
        finally:
            stop_process(server, process_group=sys.platform != "win32")
