"""Real installed development command, dependency edits and process ownership."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from pysx.development import Supervisor, WatchConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

ROOT = Path(__file__).resolve().parents[1]
SOURCE = """from pathlib import Path
from pysx import native, signal
from component import label

def app():
    count = signal(0)
    return native.Main(
        native.H1(label(), id="label"),
        native.P(Path("note.txt").read_text(), id="note"),
        native.P(Path("style.css").read_text(), id="css"),
        native.P(count, id="count"),
        native.Button("Increment", onClick=lambda _event: count.set(count() + 1), id="increment"),
    )
"""


def project(root: Path) -> None:
    (root / "user_app.py").write_text(SOURCE)
    (root / "component.py").write_text('def label():\n    return "original"\n')
    (root / "note.txt").write_text("template one")
    (root / "style.css").write_text("css one")


def get(port: int, path: str = "/") -> bytes:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
        return cast("bytes", response.read())


def wait_for(check: Callable[[], bool], log: Path, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            if check():
                return
        except OSError, urllib.error.URLError:
            pass
        time.sleep(0.05)
    pytest.fail(log.read_text())


@contextmanager
def serving(
    root: Path, command: list[str] | None = None, reload_timeout: float = 2
) -> Generator[tuple[int, Path]]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log = root / "server.log"
    env = dict(os.environ)

    if command is None:
        env["PYTHONPATH"] = str(ROOT)
    args = command or [sys.executable, "-m", "pysx.server"]
    args = [
        *args,
        "--app",
        "user_app:app",
        "--port",
        str(port),
        "--watch",
        "--watch-interval",
        "0.05",
        "--reload-timeout",
        str(reload_timeout),
    ]

    with log.open("w") as stream:
        process = subprocess.Popen(args, cwd=root, env=env, stdout=stream, stderr=stream)
        ready = False

        def started() -> bool:
            if process.poll() is not None:
                pytest.fail(log.read_text())

            return "pysx ready" in log.read_text()

        try:
            wait_for(started, log)
            ready = True
            yield port, log
        finally:
            children: list[int] = (
                cast("list[int]", json.loads(get(port, "/__pysx_dev__/status"))["workers"])
                if ready
                else []
            )

            if process.poll() is None:
                process.send_signal(signal.SIGINT)

            try:
                result = process.wait(timeout=10)

                if ready:
                    assert result == 0, log.read_text()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

            for pid in children:
                with pytest.raises(ProcessLookupError):
                    os.kill(pid, 0)
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0


def generation(port: int) -> int:
    return int(json.loads(get(port, "/__pysx_dev__/status"))["generation"])


def test_installed_serve_dependency_edits_and_failure_isolation(tmp_path: Path) -> None:
    project(tmp_path)

    with serving(tmp_path) as (port, log):
        assert b"original" in get(port)
        assert b"pysx-dev=1" in get(port)
        assert b"__pysx_dev__/status" in get(port, "/client.js")

        for filename, source, expected in (
            ("component.py", 'def label():\n    return "updated"\n', b"updated"),
            ("note.txt", "template two", b"template two"),
            ("style.css", "css two", b"css two"),
            ("component.py", "from added import text\ndef label():\n    return text\n", b"added"),
        ):
            before = generation(port)

            if filename == "component.py" and expected == b"added":
                (tmp_path / "added.py").write_text('text = "added"\n')
            (tmp_path / filename).write_text(source)

            def adopted(before: int = before, expected: bytes = expected) -> bool:
                return generation(port) > before and expected in get(port)

            wait_for(adopted, log)

        before = generation(port)
        (tmp_path / "component.py").write_text(
            'text = "mutated"\nraise RuntimeError("candidate broken")\n'
        )
        wait_for(lambda: b"candidate broken" in get(port, "/__pysx_dev__/status"), log)
        assert generation(port) == before
        assert b"added" in get(port)
        (tmp_path / "component.py").write_text('def label():\n    return "repaired"\n')
        (tmp_path / "added.py").unlink()
        wait_for(lambda: generation(port) > before and b"repaired" in get(port), log)
        before = generation(port)
        (tmp_path / "component.py").unlink()
        wait_for(lambda: b"ModuleNotFoundError" in get(port, "/__pysx_dev__/status"), log)
        assert generation(port) == before
        assert b"repaired" in get(port)
        (tmp_path / "component.py").write_text('def label():\n    return "restored"\n')
        wait_for(lambda: generation(port) > before and b"restored" in get(port), log)


@pytest.mark.parametrize(
    "broken",
    [
        "def app(:",
        "import time\ntime.sleep(20)",
        "def app():\n    raise RuntimeError('render broken')",
    ],
)
def test_installed_serve_initial_errors_timeout_and_repair(tmp_path: Path, broken: str) -> None:
    project(tmp_path)
    (tmp_path / "user_app.py").write_text(broken)

    with serving(tmp_path) as (port, log):
        wait_for(lambda: bool(json.loads(get(port, "/__pysx_dev__/status"))["error"]), log)
        assert generation(port) == 0

        with pytest.raises(urllib.error.HTTPError) as failure:
            get(port)
        assert failure.value.code == 503
        (tmp_path / "user_app.py").write_text(SOURCE)
        wait_for(lambda: b"original" in get(port), log)


def test_installed_serve_configuration_and_occupied_port(tmp_path: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    base = [sys.executable, "-m", "pysx.server", "--app", "missing:app", "--watch"]

    for options in (
        ["--watch-interval", "nan"],
        ["--host", "0.0.0.0"],
        ["--asgi"],
        ["--watch-root", str(tmp_path / "absent")],
    ):
        result = subprocess.run(
            [*base, *options], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=5
        )
        assert result.returncode != 0
        assert "watch" in result.stderr or "development" in result.stderr

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        result = subprocess.run(
            [*base, "--port", str(listener.getsockname()[1])],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 1
        assert "cannot start development server" in result.stderr


def test_installed_serve_watch_snapshot_add_delete_and_bounds(tmp_path: Path) -> None:
    project(tmp_path)
    config = WatchConfig("user_app:app", (tmp_path,))
    original = config.snapshot()
    (tmp_path / "extra.py").write_text("value = 1")
    assert config.snapshot() != original
    (tmp_path / "extra.py").unlink()
    assert config.snapshot() == original
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("value = 1")
    assert config.snapshot() == original

    with pytest.raises(ValueError, match="exceeds"):
        WatchConfig("user_app:app", (tmp_path,), max_files=1).snapshot()


def test_installed_serve_edit_during_initial_import_is_not_lost(tmp_path: Path) -> None:
    project(tmp_path)
    (tmp_path / "user_app.py").write_text(
        "from component import label\nfrom pathlib import Path\nimport time\n"
        'Path(".importing").touch()\ntime.sleep(0.5)\n' + SOURCE
    )

    def edit_during_import() -> None:
        wait_for(lambda: (tmp_path / ".importing").exists(), tmp_path / "server.log")
        (tmp_path / "component.py").write_text('def label():\n    return "startup edit"\n')

    with ThreadPoolExecutor(max_workers=1) as pool:
        editing = pool.submit(edit_during_import)

        with serving(tmp_path) as (port, log):
            editing.result(timeout=5)
            wait_for(lambda: generation(port) >= 2 and b"startup edit" in get(port), log)


def test_installed_serve_rejects_foreign_origins(tmp_path: Path) -> None:
    project(tmp_path)

    with serving(tmp_path) as (port, _log):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/__pysx_dev__/status",
            headers={"Origin": "https://foreign.test"},
        )

        with pytest.raises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(request, timeout=2)
        assert failure.value.code == 403


@pytest.mark.skipif(os.name != "posix", reason="process groups require a POSIX development host")
def test_installed_serve_rejected_import_reaps_descendants(tmp_path: Path) -> None:
    project(tmp_path)
    (tmp_path / "user_app.py").write_text(
        "import subprocess, sys\nfrom pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "Path('.childpid').write_text(str(child.pid))\n"
        "raise RuntimeError('child import failure')\n"
    )

    with serving(tmp_path) as (port, log):
        assert b"child import failure" in get(port, "/__pysx_dev__/status")
        child = int((tmp_path / ".childpid").read_text())

        def reaped() -> bool:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                return True

            return False

        wait_for(reaped, log)
        (tmp_path / "user_app.py").write_text(SOURCE)
        wait_for(lambda: b"original" in get(port), log)


def test_installed_serve_prefixed_application_and_http_methods(tmp_path: Path) -> None:
    project(tmp_path)
    (tmp_path / "user_app.py").write_text(
        SOURCE + "\nfrom pysx import Application, HostConfig\n"
        'app = Application(app, config=HostConfig(base_path="/demo"))\n'
    )

    with serving(tmp_path) as (port, log):
        assert b"original" in get(port, "/demo/")
        script = get(port, "/demo/client.js")
        assert b"__pysx_dev__/status" in script

        for path in ("/demo/", "/demo/client.js", "/__pysx_dev__/status"):
            request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="HEAD")

            with urllib.request.urlopen(request, timeout=2) as response:
                assert response.status == 200
                assert response.read() == b""

                if path.endswith("client.js"):
                    assert int(response.headers["Content-Length"]) == len(script)
        request = urllib.request.Request(f"http://127.0.0.1:{port}/demo/", method="POST")

        with pytest.raises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(request, timeout=2)
        assert failure.value.code == 405
        assert failure.value.headers["Allow"] == "GET, HEAD"
        (tmp_path / "component.py").write_text('def label():\n    return "prefixed edit"\n')
        wait_for(lambda: b"prefixed edit" in get(port, "/demo/"), log)


def test_installed_serve_authorization_is_preserved(tmp_path: Path) -> None:
    project(tmp_path)
    (tmp_path / "user_app.py").write_text(
        SOURCE + "\nfrom pysx import Application, HostConfig, SecurityPolicy\n"
        "app = Application(app, config=HostConfig(\n"
        '    principal=lambda request: request.headers.get("x-user"),\n'
        "    security=SecurityPolicy(require_principal=True),\n))\n"
    )

    with serving(tmp_path) as (port, _log):
        assert generation(port) == 1

        with pytest.raises(urllib.error.HTTPError) as failure:
            get(port)
        assert failure.value.code == 403
        request = urllib.request.Request(f"http://127.0.0.1:{port}/", headers={"x-user": "alice"})

        with urllib.request.urlopen(request, timeout=2) as response:
            assert b"original" in response.read()


def test_proxy_representation_head_length_and_single_injection(tmp_path: Path) -> None:
    project(tmp_path)

    with serving(tmp_path) as (port, _log):
        document = get(port, "/")
        script = get(port, "/client.js")
        assert document.count(b"pysx-dev=") == 1
        assert document.count(b'/client.js" defer') == 0

        for path, expected in (("/", None), ("/client.js", len(script))):
            request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="HEAD")

            with urllib.request.urlopen(request, timeout=2) as response:
                assert response.status == 200
                assert response.read() == b""

                if expected is None:
                    assert "Content-Length" not in response.headers
                else:
                    assert int(response.headers["Content-Length"]) == expected


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"PYSX_DEV_CONTROL_PORT": "9999"}, "requires PYSX_DEV_CONTROL_TOKEN"),
        (
            {"PYSX_DEV_CONTROL_PORT": "not-a-port", "PYSX_DEV_CONTROL_TOKEN": "secret"},
            "must be a port number",
        ),
    ],
)
def test_dev_control_config_rejects_half_configured_environment(
    tmp_path: Path, environment: dict[str, str], message: str
) -> None:
    project(tmp_path)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.pop("PYSX_DEV_CONTROL_PORT", None)
    env.pop("PYSX_DEV_CONTROL_TOKEN", None)
    env.update(environment)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pysx.server",
            "--app",
            "user_app:app",
            "--port",
            str(port),
            "--dev",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert message in output
    assert "KeyError" not in output
    assert "Traceback" not in output


def test_dev_control_config_token_without_port_starts_normally(tmp_path: Path) -> None:
    project(tmp_path)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.pop("PYSX_DEV_CONTROL_PORT", None)
    env["PYSX_DEV_CONTROL_TOKEN"] = "secret"

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log = tmp_path / "token_only.log"

    with log.open("w") as stream:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "pysx.server",
                "--app",
                "user_app:app",
                "--port",
                str(port),
                "--dev",
            ],
            cwd=tmp_path,
            env=env,
            stdout=stream,
            stderr=stream,
        )

        try:
            wait_for(lambda: "pysx ready" in log.read_text(), log)
            assert b"original" in get(port, "/")
        finally:
            process.terminate()
            process.wait(timeout=30)


@dataclass
class _ReplaceProbe(Supervisor):
    replaced: list[float] = field(default_factory=lambda: list[float]())

    async def replace(self) -> None:
        self.replaced.append(asyncio.get_running_loop().time())
        self.stopping.set()


def test_watch_latency_debounce_does_not_cost_an_extra_interval(tmp_path: Path) -> None:
    project(tmp_path)

    async def run() -> None:
        config = WatchConfig("user_app:app", (tmp_path,), interval=0.5, debounce=0.05)
        supervisor = _ReplaceProbe(config)
        loop = asyncio.get_running_loop()
        initial = config.snapshot()
        started = loop.time()
        task = asyncio.create_task(supervisor.watch(initial))
        (tmp_path / "component.py").write_text('def label():\n    return "edited"\n')

        try:
            await asyncio.wait_for(task, timeout=10)
        finally:
            supervisor.stopping.set()
        assert len(supervisor.replaced) == 1
        elapsed = supervisor.replaced[0] - started
        assert config.interval < elapsed < config.interval + 0.3

    asyncio.run(run())


def test_watch_latency_burst_coalesces_into_one_replacement(tmp_path: Path) -> None:
    project(tmp_path)

    async def run() -> None:
        config = WatchConfig("user_app:app", (tmp_path,), interval=0.1, debounce=0.3)
        supervisor = _ReplaceProbe(config)
        initial = config.snapshot()
        task = asyncio.create_task(supervisor.watch(initial))

        for index in range(5):
            (tmp_path / "component.py").write_text(f'def label():\n    return "edit {index}"\n')
            await asyncio.sleep(0.1)

        try:
            await asyncio.wait_for(task, timeout=10)
        finally:
            supervisor.stopping.set()
        assert len(supervisor.replaced) == 1

    asyncio.run(run())


def test_watch_latency_snapshot_reuses_unchanged_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project(tmp_path)

    for index in range(50):
        (tmp_path / f"mod_{index}.py").write_text(f"VALUE = {index}\n")
    config = WatchConfig("user_app:app", (tmp_path,), interval=0.1, debounce=0.05)
    first = config.snapshot()
    reads: list[str] = []
    original = Path.read_bytes

    def counting(self: Path) -> bytes:
        reads.append(self.name)

        return original(self)

    monkeypatch.setattr(Path, "read_bytes", counting)
    assert config.snapshot() == first
    assert reads == []
    (tmp_path / "mod_0.py").write_text("VALUE = 9\n")
    edited = config.snapshot()
    assert edited != first
    assert reads == ["mod_0.py"]
    reads.clear()
    os.utime(tmp_path / "mod_1.py", (time.time() + 5, time.time() + 5))
    assert config.snapshot() == edited
    assert reads == ["mod_1.py"]
    reads.clear()
    (tmp_path / "mod_50.py").write_text("VALUE = 50\n")
    added = config.snapshot()
    assert added.keys() - edited.keys() == {str((tmp_path / "mod_50.py").resolve())}
    assert reads == ["mod_50.py"]
    (tmp_path / "mod_50.py").unlink()
    assert config.snapshot() == edited


SLOW_FIRST_RENDER = """import time
from pysx import native

_pending = [True]


def app():
    if _pending[0]:
        _pending[0] = False
        time.sleep(3)
    return native.Main(native.H1("slow ready", id="label"))
"""


def test_swap_deadlines_slow_initial_render_is_adopted(tmp_path: Path) -> None:
    project(tmp_path)
    (tmp_path / "user_app.py").write_text(SLOW_FIRST_RENDER)

    with serving(tmp_path, reload_timeout=15) as (port, log):
        wait_for(lambda: generation(port) == 1, log)
        assert json.loads(get(port, "/__pysx_dev__/status"))["error"] is None
        assert b"slow ready" in get(port)


def test_swap_deadlines_render_beyond_the_deadline_keeps_the_previous_worker(
    tmp_path: Path,
) -> None:
    project(tmp_path)

    with serving(tmp_path, reload_timeout=3) as (port, log):
        assert b"original" in get(port)
        before = generation(port)
        (tmp_path / "user_app.py").write_text(
            "import time\nfrom pysx import native\n\n\ndef app():\n"
            "    time.sleep(30)\n    return native.Main(native.H1('never', id='label'))\n"
        )
        wait_for(lambda: bool(json.loads(get(port, "/__pysx_dev__/status"))["error"]), log)
        assert generation(port) == before
        assert b"original" in get(port)


ALWAYS_SLOW_RENDER = """import time
from pysx import native


def app():
    time.sleep(3)
    return native.Main(native.H1("slow every time", id="label"))
"""


def test_swap_deadlines_slow_proxied_render_is_not_dropped(tmp_path: Path) -> None:
    project(tmp_path)
    (tmp_path / "user_app.py").write_text(ALWAYS_SLOW_RENDER)

    with serving(tmp_path, reload_timeout=15) as (port, log):
        wait_for(lambda: generation(port) == 1, log)

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=20) as response:
            assert response.status == 200
            assert b"slow every time" in response.read()


def test_watch_latency_push_channel_reports_generations(tmp_path: Path) -> None:
    project(tmp_path)

    with serving(tmp_path) as (port, log):
        assert b"__pysx_dev__/events" in get(port, "/client.js")

        async def listen() -> list[int]:
            from websockets.asyncio.client import connect

            seen: list[int] = []

            async with connect(f"ws://127.0.0.1:{port}/__pysx_dev__/events") as socket:
                payload = json.loads(await socket.recv())
                seen.append(int(payload["generation"]))
                (tmp_path / "component.py").write_text('def label():\n    return "pushed"\n')

                while True:
                    payload = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
                    seen.append(int(payload["generation"]))

                    if seen[-1] > seen[0]:
                        assert payload["error"] is None

                        return seen

        generations = asyncio.run(listen())
        assert generations[0] == 1
        assert generations[-1] == 2
        wait_for(lambda: b"pushed" in get(port), log)


COUNTER = """from pysx import native, signal


def app():
    count = signal(0)
    return native.Main(
        native.P(count, id="count"),
        native.Button("+", onClick=lambda _event: count.set(count() + 1), id="increment"),
    )
"""


def test_volatile_preserving_reconciles_without_a_browser_reload(tmp_path: Path) -> None:
    project(tmp_path)
    (tmp_path / "user_app.py").write_text(COUNTER)

    with serving(tmp_path) as (port, log):
        assert b'id="count"' in get(port, "/")
        before = json.loads(get(port, "/__pysx_dev__/status"))
        assert before["fresh_generation"] == before["generation"]
        (tmp_path / "component.py").write_text('def label():\n    return "swapped"\n')

        def advanced() -> bool:
            return int(json.loads(get(port, "/__pysx_dev__/status"))["generation"]) > int(
                before["generation"]
            )

        wait_for(advanced, log)
        after = json.loads(get(port, "/__pysx_dev__/status"))
        assert after["carried_slots"] >= 1
        assert after["dropped_slots"] == 0
        assert after["fresh_generation"] < after["generation"]


async def _resume(port: int, cookie: str) -> dict[str, object]:
    from websockets.asyncio.client import connect

    url = f"ws://127.0.0.1:{port}/ws?protocol=3&resume=1&location=%2F"

    async with connect(url, additional_headers={"Cookie": cookie}) as socket:
        await socket.send(
            json.dumps(
                {
                    "t": "hello",
                    "version": 3,
                    "resume": "stale-token",
                    "epoch": "stale-epoch",
                    "state_rev": 0,
                }
            )
        )

        return cast("dict[str, object]", json.loads(await asyncio.wait_for(socket.recv(), 15)))


def test_volatile_resume_rebuilds_a_reconcilable_attachment(tmp_path: Path) -> None:
    project(tmp_path)

    with serving(tmp_path) as (port, log):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as response:
            cookie = response.headers.get("Set-Cookie", "").split(";")[0]
            assert b"original" in response.read()
        cold = asyncio.run(_resume(port, cookie))
        assert cold["t"] == "init"
        assert cold["fresh"] is True
        assert "original" in cast("str", cold["html"])
        before = generation(port)
        (tmp_path / "component.py").write_text('def label():\n    return "repaired"\n')
        wait_for(lambda: generation(port) > before, log)
        swapped = asyncio.run(_resume(port, cookie))
        assert swapped["t"] == "init"
        assert swapped["fresh"] is True
        assert "repaired" in cast("str", swapped["html"])
        assert "original" not in cast("str", swapped["html"])


def test_volatile_resume_outside_development_still_closes(tmp_path: Path) -> None:
    from websockets.exceptions import ConnectionClosed

    project(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log = tmp_path / "plain.log"

    with log.open("w") as stream:
        process = subprocess.Popen(
            [sys.executable, "-m", "pysx.server", "--app", "user_app:app", "--port", str(port)],
            cwd=tmp_path,
            env=env,
            stdout=stream,
            stderr=stream,
        )

        try:
            wait_for(lambda: "pysx ready" in log.read_text(), log)

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as response:
                cookie = response.headers.get("Set-Cookie", "").split(";")[0]
                response.read()

            with pytest.raises(ConnectionClosed) as failure:
                asyncio.run(_resume(port, cookie))
            assert failure.value.rcvd is not None
            assert failure.value.rcvd.code == 4004
        finally:
            process.terminate()
            process.wait(timeout=30)


def test_preservation_report_lists_resets_and_cold_discards_state(tmp_path: Path) -> None:
    project(tmp_path)
    (tmp_path / "user_app.py").write_text(COUNTER)

    with serving(tmp_path) as (port, log):
        assert b'id="count"' in get(port, "/")
        before = generation(port)
        (tmp_path / "component.py").write_text('def label():\n    return "swapped"\n')
        wait_for(lambda: generation(port) > before, log)
        after = json.loads(get(port, "/__pysx_dev__/status"))
        assert after["carried_slots"] >= 1
        assert "preserved" in after
        assert isinstance(after["reset"], list)
        assert get(port, "/__pysx_dev__/cold") == b"cold"
        cleared = json.loads(get(port, "/__pysx_dev__/status"))
        assert cleared["carried_slots"] == 0
        assert cleared["dropped_slots"] == 0


def test_preservation_opt_out_restores_the_reloading_contract(tmp_path: Path) -> None:
    project(tmp_path)
    (tmp_path / "user_app.py").write_text(COUNTER)
    command = [sys.executable, "-m", "pysx.server", "--no-preserve-state"]

    with serving(tmp_path, command=command) as (port, log):
        assert b'id="count"' in get(port, "/")
        before = json.loads(get(port, "/__pysx_dev__/status"))
        (tmp_path / "component.py").write_text('def label():\n    return "swapped"\n')
        wait_for(
            lambda: (
                int(json.loads(get(port, "/__pysx_dev__/status"))["generation"])
                > int(before["generation"])
            ),
            log,
        )
        after = json.loads(get(port, "/__pysx_dev__/status"))
        assert after["carried_slots"] == 0
        assert after["fresh_generation"] == after["generation"]


def test_installed_serve_injected_client_bundle_parses(tmp_path: Path) -> None:
    node = shutil.which("node")

    if node is None:
        pytest.skip("node is unavailable")
    project(tmp_path)

    with serving(tmp_path) as (port, _log):
        bundle = tmp_path / "bundle.js"
        bundle.write_bytes(get(port, "/client.js"))
        result = subprocess.run(
            [node, "--check", str(bundle)], capture_output=True, text=True, timeout=60, check=False
        )
        assert result.returncode == 0, result.stderr
