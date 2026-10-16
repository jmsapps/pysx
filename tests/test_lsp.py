"""Real framed resident-server sessions, without executing application modules."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from lsprotocol import types

from pysx.compiler import analyze
from pysx.editor_types import diagnostics
from pysx.lsp_backend import Stamp
from pysx.lsp_workspace import WorkspaceIndex, position

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from pysx.compiler import Compilation
    from pysx.editor_model import Buffer

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    "from pysx import Fragment, pysx\n"
    "def Panel(*, title: str) -> Fragment:\n"
    "    return pysx(t'p: {title}')\n"
    "view = pysx(t'Panel(title={42})')\n"
)


class Client:
    def __init__(
        self,
        root: Path,
        encoding: str = "utf-16",
        options: dict[str, object] | None = None,
        command: list[str] | None = None,
    ) -> None:
        self.process = subprocess.Popen(
            command or [sys.executable, "-m", "pysx.lsp"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.history: list[dict[str, Any]] = []
        self.ident = 0
        self.errors: deque[str] = deque(maxlen=30)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_errors, daemon=True).start()
        result = self.request(
            "initialize",
            {
                "processId": None,
                "initializationOptions": options,
                "rootUri": root.as_uri(),
                "workspaceFolders": [{"uri": root.as_uri(), "name": root.name}],
                "capabilities": {
                    "general": {"positionEncodings": [encoding]},
                    "textDocument": {"publishDiagnostics": {"versionSupport": True}},
                },
            },
        )
        assert isinstance(result, dict)
        self.initialization = cast("dict[str, Any]", result)
        assert cast("dict[str, Any]", result)["capabilities"]["positionEncoding"] == encoding
        self.send("initialized", {})

    def _read_errors(self) -> None:
        stream = self.process.stderr
        assert stream is not None

        for line in iter(stream.readline, b""):
            self.errors.append(line.decode(errors="replace"))

    def _read(self) -> None:
        stream = self.process.stdout
        assert stream is not None

        while True:
            headers: dict[str, str] = {}

            while line := stream.readline():
                if line in (b"\r\n", b"\n"):
                    break
                key, value = line.decode().split(":", 1)
                headers[key.lower()] = value.strip()

            if not headers:
                return
            self.queue.put(json.loads(stream.read(int(headers["content-length"]))))

    def send(self, method: str, params: object, *, request: bool = False) -> int:
        self.ident += 1
        payload: dict[str, object] = {"jsonrpc": "2.0", "method": method, "params": params}

        if request:
            payload["id"] = self.ident
        data = json.dumps(payload).encode()
        stream = self.process.stdin
        assert stream is not None
        stream.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        stream.flush()

        return self.ident

    def wait(self, predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        deadline = time.monotonic() + 30

        while time.monotonic() < deadline:
            try:
                message = self.queue.get(timeout=min(1, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                assert self.process.poll() is None, "language server exited unexpectedly"

                continue
            self.history.append(message)

            if predicate(message):
                return message
        pytest.fail(f"Language server response timed out: {self.history[-4:]} {list(self.errors)}")

    def request(self, method: str, params: object) -> object:
        result = self.call(method, params)
        assert "error" not in result, result

        return cast("object", result.get("result"))

    def call(self, method: str, params: object) -> dict[str, Any]:
        """Full response envelope, so refusal paths can assert on the error."""
        ident = self.send(method, params, request=True)

        return self.wait(lambda message: message.get("id") == ident)

    def open(self, path: Path, source: str = SOURCE) -> None:
        self.send(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": path.as_uri(),
                    "languageId": "python",
                    "version": 1,
                    "text": source,
                }
            },
        )

    def change(self, path: Path, source: str, version: int) -> None:
        self.send(
            "textDocument/didChange",
            {
                "textDocument": {"uri": path.as_uri(), "version": version},
                "contentChanges": [{"text": source}],
            },
        )

    def diagnostic(self, path: Path, version: int, code: str | None) -> dict[str, Any]:
        def matches(message: dict[str, Any]) -> bool:
            params = message.get("params", {})

            return (
                message.get("method") == "textDocument/publishDiagnostics"
                and params.get("uri") == path.as_uri()
                and params.get("version") == version
                and (
                    not params["diagnostics"]
                    if code is None
                    else any(item.get("code") == code for item in params["diagnostics"])
                )
            )

        return cast("dict[str, Any]", self.wait(matches)["params"])

    def status(self) -> dict[str, Any]:
        return cast("dict[str, Any]", self.request("pysx/status", {}))

    def settled(self, predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        deadline = time.monotonic() + 30

        while time.monotonic() < deadline:
            status = self.status()

            if predicate(status):
                return status
            time.sleep(0.02)
        pytest.fail("Language server did not reach the expected state")

    def hover(self, path: Path, line: int, character: int) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self.request(
                "textDocument/hover",
                {
                    "textDocument": {"uri": path.as_uri()},
                    "position": {"line": line, "character": character},
                },
            ),
        )

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                self.request("shutdown", None)
                self.send("exit", None)
                assert self.process.wait(timeout=10) == 0
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)

            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream is not None:
                    stream.close()


@pytest.fixture
def client(tmp_path: Path) -> Iterator[Client]:
    instance = Client(tmp_path)

    try:
        yield instance
    finally:
        instance.close()


def test_resident_protocol_lifecycle_unsaved_and_stale_versions(
    tmp_path: Path,
    client: Client,
) -> None:
    path = tmp_path / "view.py"
    # The new file exists only in the open buffer. Analysis must not execute it.
    marker = tmp_path / "executed"
    source = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + SOURCE
    client.open(path, source)
    first = client.diagnostic(path, 1, "reportArgumentType")
    assert first["diagnostics"]
    state = client.status()["workspaces"][0]
    assert state["starts"] == 1
    assert not marker.exists()
    corrected = source.replace("{42}", "{'valid'}")
    client.change(path, corrected, 2)
    client.diagnostic(path, 2, None)
    client.change(path, source, 1)
    current = client.status()["workspaces"][0]
    assert current["generation"] == 2
    assert current["pid"] == state["pid"]
    client.send("textDocument/didSave", {"textDocument": {"uri": path.as_uri()}})
    assert not marker.exists()


@pytest.mark.parametrize(("encoding", "units"), [("utf-16", 2), ("utf-8", 4), ("utf-32", 1)])
def test_resident_protocol_non_bmp_edits(
    tmp_path: Path,
    encoding: str,
    units: int,
) -> None:
    client = Client(tmp_path, encoding)
    path = tmp_path / "view.py"
    source = SOURCE.replace("view = ", "emoji = '😀'; view = ")

    try:
        client.open(path, source)
        reported = client.diagnostic(path, 1, "reportArgumentType")
        findings = [
            item for item in reported["diagnostics"] if item.get("code") == "reportArgumentType"
        ]
        assert findings[0]["range"]["start"]["line"] == 3
        prefix = source.splitlines()[3].split("42")[0]
        expected = len(prefix.encode(encoding.replace("utf-16", "utf-16-le")))

        if encoding == "utf-16":
            expected //= 2
        elif encoding == "utf-32":
            expected = len(prefix)
        assert findings[0]["range"]["start"]["character"] == expected
        client.send(
            "textDocument/didChange",
            {
                "textDocument": {"uri": path.as_uri(), "version": 2},
                "contentChanges": [
                    {
                        "range": {
                            "start": {"line": 3, "character": 9},
                            "end": {"line": 3, "character": 9 + units},
                        },
                        "text": "ok",
                    }
                ],
            },
        )
        client.diagnostic(path, 2, "reportArgumentType")
        assert client.status()["workspaces"][0]["starts"] == 1
    finally:
        client.close()


def test_resident_protocol_incremental_imported_signature_and_unrelated_edit(
    tmp_path: Path,
    client: Client,
) -> None:
    producer, consumer, other = [tmp_path / name for name in ("parts.py", "view.py", "other.py")]
    producer_source = SOURCE[: SOURCE.index("view =")]
    producer.write_text(producer_source)
    consumer_source = (
        "from pysx import pysx\nfrom parts import Panel\nview = pysx(t'Panel(title={42})')\n"
    )
    consumer.write_text(consumer_source)
    other.write_text("value = 1\n")
    client.open(consumer, consumer_source)
    client.diagnostic(consumer, 1, "reportArgumentType")
    baseline = client.status()["workspaces"][0]
    client.open(producer, producer_source)
    client.change(producer, producer_source.replace("title: str", "title: int"), 2)
    client.diagnostic(consumer, 1, None)
    current = client.status()["workspaces"][0]
    assert current["pid"] == baseline["pid"]
    assert set(current["changed"]) == {"parts.py", "view.py"}
    assert "other.py" not in current["republished"]
    assert "int" in str(client.hover(consumer, 2, 14))
    client.open(other, "value = 2\n")
    # A feature request waits for projection synchronization, never for a full diagnostic sweep.
    assert "int" in str(client.hover(consumer, 2, 14))
    assert client.status()["workspaces"][0]["changed"] == ["other.py"]


def test_resident_protocol_coherence_and_protocol_cancellation(
    tmp_path: Path,
    client: Client,
) -> None:
    old = Stamp(1, 1)
    with pytest.raises(ValueError, match="superseded"):
        old.require_current(Stamp(2, 1))
    old.require_current(Stamp(1, 1))
    with pytest.raises(ValueError, match="superseded"):
        old.require_current(Stamp(1, 2))
    path = tmp_path / "view.py"
    client.open(path)
    ident = client.send(
        "textDocument/hover",
        {
            "textDocument": {"uri": path.as_uri()},
            "position": {"line": 3, "character": 14},
        },
        request=True,
    )
    client.send("$/cancelRequest", {"id": ident})
    response = client.wait(lambda message: message.get("id") == ident)
    assert response["error"]["code"] == -32800
    client.diagnostic(path, 1, "reportArgumentType")
    assert "Panel" in str(client.hover(path, 3, 14))
    assert client.status()["workspaces"][0]["starts"] == 1


def test_resident_protocol_scheduling_close_and_partial_input(
    tmp_path: Path,
    client: Client,
) -> None:
    path = tmp_path / "view.py"
    path.write_text(SOURCE)
    client.open(path, "from pysx import pysx\nview = pysx(t'input(\n")
    client.wait(lambda message: message.get("method") == "textDocument/publishDiagnostics")
    client.change(path, SOURCE, 2)
    client.diagnostic(path, 2, "reportArgumentType")
    client.change(path, SOURCE.replace("{42}", "{'valid'}"), 3)
    client.diagnostic(path, 3, None)
    client.send("textDocument/didClose", {"textDocument": {"uri": path.as_uri()}})
    client.wait(
        lambda message: (
            message.get("method") == "textDocument/publishDiagnostics"
            and not message["params"]["diagnostics"]
        )
    )
    assert client.status()["workspaces"] == []


def test_resident_protocol_incremental_backend_restart(tmp_path: Path, client: Client) -> None:
    path = tmp_path / "view.py"
    client.open(path)
    client.diagnostic(path, 1, "reportArgumentType")
    state = client.status()["workspaces"][0]
    os.kill(state["pid"], 9)
    client.change(path, SOURCE.replace("{42}", "{43}"), 2)
    client.diagnostic(path, 2, "reportArgumentType")
    recovered = client.status()["workspaces"][0]
    assert recovered["starts"] == 2
    assert recovered["epoch"] > state["epoch"]
    assert recovered["pid"] != state["pid"]


def test_resident_protocol_incremental_config_and_interpreter(
    tmp_path: Path,
    client: Client,
) -> None:
    path = tmp_path / "view.py"
    configuration = tmp_path / "pyrightconfig.json"
    configuration.write_text('{"reportArgumentType": "error"}')
    client.open(path)
    client.diagnostic(path, 1, "reportArgumentType")
    baseline = client.status()["workspaces"][0]
    configuration.write_text('{"reportArgumentType": "none"}')
    client.send(
        "workspace/didChangeWatchedFiles",
        {
            "changes": [
                {"uri": configuration.as_uri(), "type": 2},
            ]
        },
    )
    current = client.settled(
        lambda state: (
            state["workspaces"][0]["generation"] > baseline["generation"]
            and "view.py" in state["workspaces"][0]["republished"]
        )
    )["workspaces"][0]
    publications = [
        item["params"]["diagnostics"]
        for item in client.history
        if item.get("method") == "textDocument/publishDiagnostics"
        and item["params"]["uri"] == path.as_uri()
    ]
    assert all(item.get("code") != "reportArgumentType" for item in publications[-1])
    assert any(item.get("code") == "component-prop-value" for item in publications[-1])
    assert current["generation"] > baseline["generation"]
    assert current["pid"] != baseline["pid"]
    assert current["epoch"] > baseline["epoch"]
    selected = str(Path(sys.executable).parent) + "/./" + Path(sys.executable).name
    client.send("workspace/didChangeConfiguration", {"settings": {"pythonPath": selected}})
    restarted = client.settled(
        lambda state: bool(state["workspaces"]) and state["workspaces"][0]["starts"] == 3
    )
    assert restarted["workspaces"][0]["epoch"] > baseline["epoch"]


@pytest.mark.parametrize("size", [4, 16])
def test_resident_protocol_incremental_bounded_affected_set(
    tmp_path: Path,
    client: Client,
    size: int,
) -> None:
    producer = tmp_path / "parts.py"
    producer_source = SOURCE[: SOURCE.index("view =")]
    producer.write_text(producer_source)

    for number in range(size):
        (tmp_path / f"leaf{number}.py").write_text(f"value = {number}\n")
    path = tmp_path / "view.py"
    source = "from parts import Panel\nfrom pysx import pysx\nview = pysx(t'Panel(title={42})')\n"
    path.write_text(source)
    client.open(path, source)
    client.diagnostic(path, 1, "reportArgumentType")
    client.settled(lambda status: len(status["workspaces"][0]["republished"]) >= size + 2)
    client.open(producer, producer_source)
    client.change(producer, producer_source.replace("title: str", "title: int"), 2)
    client.diagnostic(path, 1, None)
    # Wait for the affected files themselves rather than for a count: a count
    # is satisfied by whichever publishes land first, so it can sample the set
    # before the edited consumer is in it. Any unrelated file still fails the
    # assertion below, so the bound itself is unchanged.
    affected = {"parts.py", "view.py"}
    state = client.settled(lambda status: affected <= set(status["workspaces"][0]["republished"]))
    assert set(state["workspaces"][0]["changed"]) == affected
    assert set(state["workspaces"][0]["republished"]) == affected
    assert state["workspaces"][0]["starts"] == 1


def test_resident_protocol_incremental_equals_fresh_cli(tmp_path: Path, client: Client) -> None:
    path = tmp_path / "view.py"
    path.write_text(SOURCE.replace("{42}", "{'valid'}"))
    client.open(path, path.read_text())
    client.diagnostic(path, 1, None)
    client.change(path, SOURCE, 2)
    resident = client.diagnostic(path, 2, "reportArgumentType")
    index = WorkspaceIndex(tmp_path)

    try:
        snapshot = index.build({str(path): {"source": SOURCE, "version": 2}}, Stamp(1, 1))
        index.write_config()

        for item in snapshot.files:
            (index.projected / item.relative).write_text(item.text)
        fresh = diagnostics(index.projected, index.directory)
        findings = cast("list[dict[str, Any]]", fresh["generalDiagnostics"])
        expected = [item for item in findings if item.get("rule") == "reportArgumentType"]
        actual = [
            item for item in resident["diagnostics"] if item.get("code") == "reportArgumentType"
        ]
        assert len(actual) == len(expected) == 1
        assert actual[0]["message"] == expected[0]["message"]
        target = expected[0]["range"]
        mapped = snapshot.files[0].original_range(
            types.Range(
                types.Position(**target["start"]),
                types.Position(**target["end"]),
            )
        )
        assert mapped is not None
        assert actual[0]["range"] == {
            "start": {"line": mapped.start.line, "character": mapped.start.character},
            "end": {"line": mapped.end.line, "character": mapped.end.character},
        }
    finally:
        index.close()


def test_resident_protocol_coherence_overlapping_dependency_edits(
    tmp_path: Path,
    client: Client,
) -> None:
    path = tmp_path / "view.py"
    client.open(path)
    client.diagnostic(path, 1, "reportArgumentType")
    requests: list[int] = []

    for version in range(2, 14):
        requests.append(
            client.send(
                "textDocument/hover",
                {
                    "textDocument": {"uri": path.as_uri()},
                    "position": {"line": 3, "character": 14},
                },
                request=True,
            )
        )
        client.change(path, SOURCE.replace("title: str", "title: int"), version)
    responses: list[dict[str, Any]] = []

    while len(responses) < len(requests):
        responses.append(client.wait(lambda message: message.get("id") in requests))
    assert all(
        "result" in item or item.get("error", {}).get("code") == -32801 for item in responses
    )
    assert any(item.get("error", {}).get("code") == -32801 for item in responses)
    assert "int" in str(client.hover(path, 3, 14))
    assert client.status()["workspaces"][0]["starts"] == 1


def test_resident_protocol_scheduling_repeated_edits_bound_cache(
    tmp_path: Path,
    client: Client,
) -> None:
    path = tmp_path / "view.py"
    client.open(path)
    client.diagnostic(path, 1, "reportArgumentType")

    for version in range(2, 14):
        source = SOURCE.replace("{42}", "{'valid'}") if version % 2 == 0 else SOURCE
        client.change(path, source, version)
        client.diagnostic(path, version, None if version % 2 == 0 else "reportArgumentType")
    state = client.status()["workspaces"][0]
    assert state["starts"] == 1
    assert state["cachedDocuments"] == state["openDocuments"] == 1
    client.send("textDocument/didClose", {"textDocument": {"uri": path.as_uri()}})
    client.settled(lambda status: not status["workspaces"])


def test_resident_protocol_missing_extra_is_actionable() -> None:
    script = (
        "import importlib.util, runpy\n"
        "original = importlib.util.find_spec\n"
        "importlib.util.find_spec = lambda name: None if name == 'pygls' else original(name)\n"
        "runpy.run_module('pysx.lsp', run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "pysx[lsp]" in result.stderr


def test_resident_protocol_python_regions_remain_owned_by_python(
    tmp_path: Path,
    client: Client,
) -> None:
    path = tmp_path / "view.py"
    client.open(path, "bad: int = 'wrong'\n" + SOURCE.replace("{42}", "{'valid'}"))
    client.settled(
        lambda status: (
            bool(status["workspaces"]) and "view.py" in status["workspaces"][0]["republished"]
        )
    )
    publications = [
        item["params"]["diagnostics"]
        for item in client.history
        if item.get("method") == "textDocument/publishDiagnostics"
    ]
    assert publications[-1] == []
    assert client.hover(path, 0, 1) is None


def test_resident_protocol_rejects_oversized_open_and_recovers(
    tmp_path: Path,
    client: Client,
) -> None:
    client.open(tmp_path / "large.py", "#" + "x" * (8 * 1024 * 1024))
    assert client.status()["workspaces"] == []
    path = tmp_path / "view.py"
    client.open(path)
    client.diagnostic(path, 1, "reportArgumentType")


def test_resident_protocol_scheduling_disconnect_reaps_backend(tmp_path: Path) -> None:
    client = Client(tmp_path)
    path = tmp_path / "view.py"

    try:
        client.open(path)
        client.diagnostic(path, 1, "reportArgumentType")
        pid = str(client.status()["workspaces"][0]["pid"])
        assert client.process.stdin is not None
        client.process.stdin.close()
        client.process.wait(timeout=10)
        command = (
            ["ps", "-o", "stat=", "-p", pid]
            if os.name == "posix"
            else ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"]
        )
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=5)

        if os.name == "posix":
            assert not result.stdout.strip() or result.stdout.strip().startswith("Z")
        else:
            assert pid not in result.stdout
    finally:
        client.close()


def test_client_lifecycle_abrupt_server_death_reaps_backend(tmp_path: Path) -> None:
    client = Client(tmp_path)

    try:
        client.open(tmp_path / "view.py")
        client.diagnostic(tmp_path / "view.py", 1, "reportArgumentType")
        status = client.status()
        assert status["processId"] == client.process.pid
        assert status["interpreter"] == sys.executable
        pid = str(status["workspaces"][0]["pid"])
        client.process.kill()
        client.process.wait(timeout=10)
        deadline = time.monotonic() + 10

        while True:
            command = (
                ["ps", "-o", "stat=", "-p", pid]
                if os.name == "posix"
                else ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"]
            )
            result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=5)
            dead = (
                not result.stdout.strip() or result.stdout.strip().startswith("Z")
                if os.name == "posix"
                else pid not in result.stdout
            )

            if dead:
                break
            assert time.monotonic() < deadline, "type backend survived its owning server"
            time.sleep(0.05)
    finally:
        client.close()


def test_client_verify_server_advertises_every_bundled_client_capability(
    client: Client,
) -> None:
    """The bundled client registers these; a missing capability is a silent dead feature."""
    result = client.request(
        "initialize",
        {
            "processId": None,
            "rootUri": None,
            "capabilities": {"general": {"positionEncodings": ["utf-16"]}},
        },
    )
    assert isinstance(result, dict)
    capabilities = cast("dict[str, Any]", result)["capabilities"]

    for name in (
        "completionProvider",
        "signatureHelpProvider",
        "hoverProvider",
        "definitionProvider",
        "referencesProvider",
        "renameProvider",
        "documentFormattingProvider",
        "documentRangeFormattingProvider",
        "codeActionProvider",
        "foldingRangeProvider",
        "documentSymbolProvider",
        "selectionRangeProvider",
        "semanticTokensProvider",
    ):
        assert capabilities.get(name), f"{name} is not advertised"
    legend = capabilities["semanticTokensProvider"]["legend"]
    assert "keyword" in legend["tokenTypes"]


def test_client_verify_server_starts_from_an_arbitrary_working_directory(
    tmp_path: Path,
) -> None:
    """The client launches the selected interpreter, never a checkout-relative path."""
    process = subprocess.Popen(
        [sys.executable, "-m", "pysx.lsp"],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )

    try:
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "processId": None,
                    "rootUri": tmp_path.as_uri(),
                    "workspaceFolders": [{"uri": tmp_path.as_uri(), "name": tmp_path.name}],
                    "capabilities": {"general": {"positionEncodings": ["utf-16"]}},
                },
            }
        ).encode()
        assert process.stdin is not None
        process.stdin.write(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
        process.stdin.flush()
        assert process.stdout is not None
        header = process.stdout.readline()
        assert header.lower().startswith(b"content-length:")
    finally:
        process.kill()
        process.wait(timeout=10)

        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def test_client_verify_stdout_carries_protocol_frames_only(tmp_path: Path, client: Client) -> None:
    """A stray print on stdout corrupts framing; logging must go to stderr."""
    clean = (
        "from pysx import Fragment, pysx\n"
        "def Panel(*, title: str) -> Fragment:\n"
        "    return pysx(t'p: {title}')\n"
        "view = pysx(t'Panel(title={\"ok\"})')\n"
    )
    client.open(tmp_path / "view.py", clean)
    client.diagnostic(tmp_path / "view.py", 1, None)
    assert all("Content-Length" not in line for line in client.errors)
    assert all(message.get("jsonrpc") == "2.0" for message in client.history)


def test_editor_surface_crashed_backend_reports_degraded_status_and_recovers(
    tmp_path: Path, client: Client
) -> None:
    """A killed backend must be visible as status, then recover, not fail silently."""
    path = tmp_path / "view.py"
    client.open(path)
    client.diagnostic(path, 1, "reportArgumentType")
    before = client.status()["workspaces"][0]
    assert before["ready"] is True
    assert before["error"] is None
    os.kill(before["pid"], 9)
    client.change(path, SOURCE.replace("{42}", "{43}"), 2)
    client.diagnostic(path, 2, "reportArgumentType")
    after = client.status()["workspaces"][0]
    assert after["starts"] == before["starts"] + 1
    assert after["pid"] != before["pid"]
    assert after["error"] is None, "recovered analysis must not stay degraded"
    assert after["ready"] is True


def test_editor_surface_closing_every_document_releases_the_backend(
    tmp_path: Path, client: Client
) -> None:
    path = tmp_path / "view.py"
    client.open(path)
    client.diagnostic(path, 1, "reportArgumentType")
    pid = client.status()["workspaces"][0]["pid"]
    client.send("textDocument/didClose", {"textDocument": {"uri": path.as_uri()}})
    client.settled(lambda state: not state["workspaces"])
    deadline = time.monotonic() + 15

    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.05)
    pytest.fail("the analysis backend outlived its last open document")


def test_editor_surface_every_advertised_provider_answers(tmp_path: Path, client: Client) -> None:
    """An advertised capability that returns nothing is a dead feature."""
    path = tmp_path / "view.py"
    source = (
        "from pysx import Fragment, pysx\n"
        "def Panel(*, title: str) -> Fragment:\n"
        "    return pysx(t'p: {title}')\n"
        "view = pysx(t'''\n"
        "    div:\n"
        '          Panel(title="x")\n'
        "''')\n"
    )
    client.open(path, source)
    client.diagnostic(path, 1, None)
    document = {"textDocument": {"uri": path.as_uri()}}
    at = source.index('Panel(title="x"')
    point = position(source, at)
    place = {**document, "position": {"line": point.line, "character": point.character}}
    assert client.request("textDocument/hover", place) is not None
    assert client.request("textDocument/definition", place)
    assert client.request(
        "textDocument/references", {**place, "context": {"includeDeclaration": True}}
    )
    assert client.request("textDocument/documentSymbol", document)
    assert client.request("textDocument/foldingRange", document)
    assert cast("dict[str, Any]", client.request("textDocument/semanticTokens/full", document))[
        "data"
    ]
    assert client.request(
        "textDocument/selectionRange",
        {**document, "positions": [place["position"]]},
    )
    assert client.request(
        "textDocument/formatting", {**document, "options": {"tabSize": 4, "insertSpaces": True}}
    )
    assert client.request(
        "textDocument/codeAction",
        {
            **document,
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 0},
            },
            "context": {"diagnostics": []},
        },
    )
    assert client.request("textDocument/prepareRename", place) is not None


def test_buffer_readiness_selective_models_reuse_full_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library.py"
    library.write_text(SOURCE[: SOURCE.index("view =")])
    consumer = tmp_path / "view.py"
    source = (
        "import os\nfrom library import Panel\nfrom pysx import pysx\n"
        "view = pysx(t'Panel(title=\"ok\")')\n"
    )
    consumer.write_text(source)
    unrelated = tmp_path / "unrelated.py"
    unrelated.write_text("raise RuntimeError('source analysis never executes this')\n")
    analyzed: list[str] = []
    original = analyze

    def recorded(
        source: str,
        filename: str,
        *,
        workspace_roots: tuple[Path, ...] = (),
        buffers: Mapping[str, str] | None = None,
        package: str | None = None,
    ) -> Compilation:
        analyzed.append(filename)

        return original(
            source, filename, workspace_roots=workspace_roots, buffers=buffers, package=package
        )

    monkeypatch.setattr("pysx.editor_model.analyze", recorded)
    index = WorkspaceIndex(tmp_path)

    try:
        first = index.build({}, Stamp(1, 0), selected={str(consumer)})
        assert [item.filename for item in first.files] == [str(consumer)]
        assert analyzed == [str(consumer)]
        assert index.revision + ".library" in first.files[0].text
        assert str(library) in index.dependencies(str(consumer))
        second = index.build({}, Stamp(1, 0), selected={str(library)})
        assert [item.filename for item in second.files] == [str(library)]
        assert index.static_diagnostics(str(consumer))
        full = index.build({}, Stamp(1, 0))
        assert {item.filename for item in full.files} == set(index.files({}))
        assert analyzed == [str(consumer), str(library), str(unrelated)]
        index.build({}, Stamp(2, 0), selected={str(consumer)})
        assert not index.affected(full, partial=True)
        updated = source.replace('title="ok"', 'title="new"')
        buffers: dict[str, Buffer] = {str(consumer): {"source": updated, "version": 2}}
        changed = index.build(buffers, Stamp(2, 0), selected={str(consumer)})
        assert changed.files[0].version == 2
        assert changed.files[0].source == updated
        assert index.affected(full, partial=True) == {str(consumer)}
    finally:
        index.close()


def test_buffer_readiness_import_dependencies_include_unsaved_relative_reexports(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    nested = package / "nested"
    nested.mkdir(parents=True)
    sources = {
        package / "__init__.py": "from .parts import Panel\n",
        package / "parts.py": SOURCE[: SOURCE.index("view =")],
        nested / "__init__.py": "",
        nested / "view.py": "from .. import Panel\nfrom . import fresh\nvalue = Panel\n",
        tmp_path / "unrelated.py": "value = 1\n",
    }

    for path, source in sources.items():
        path.write_text(source)
    fresh = nested / "fresh.py"
    buffers: dict[str, Buffer] = {str(fresh): {"source": "value = 'unsaved'\n", "version": 7}}
    index = WorkspaceIndex(tmp_path)

    try:
        view = nested / "view.py"
        assert str(fresh) in index.files(buffers)
        index.build(buffers, Stamp(1, 0), selected={str(view)})
        dependencies = index.dependencies(str(view))
        assert dependencies == {
            str(package / "__init__.py"),
            str(nested / "__init__.py"),
            str(fresh),
        }
        index.build(buffers, Stamp(1, 0), selected=dependencies)
        assert str(package / "parts.py") in index.dependencies(str(package / "__init__.py"))
        assert not fresh.exists()
        with pytest.raises(ValueError, match="selected sources"):
            index.build(buffers, Stamp(1, 0), selected={str(tmp_path / "missing.py")})
    finally:
        index.close()


def test_buffer_readiness_sdk_imports_keep_canonical_identity(tmp_path: Path) -> None:
    sdk = tmp_path / "pysx"
    sdk.mkdir()
    (sdk / "__init__.py").write_text("raise RuntimeError('SDK sources are never executed')\n")
    library = tmp_path / "library.py"
    library.write_text(SOURCE[: SOURCE.index("view =")])
    consumer = tmp_path / "view.py"
    consumer.write_text(
        "from pysx import Fragment, pysx\nfrom library import Panel\n"
        "def view() -> Fragment:\n    return pysx(t'Panel(title=\"ok\")')\n"
    )
    index = WorkspaceIndex(tmp_path)

    try:
        snapshot = index.build({}, Stamp(1, 0), selected={str(consumer)})
        projected = snapshot.files[0].text
        assert "from pysx import Fragment, pysx" in projected
        assert "import pysx._compiler_runtime" in projected
        assert index.revision + ".pysx" not in projected
        assert index.revision + ".library" in projected
        assert index.dependencies(str(consumer)) == {str(library)}
    finally:
        index.close()
