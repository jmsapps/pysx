"""Resident maintained type service; backend cancellation is deliberately not forwarded."""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from uuid import uuid4

from lsprotocol import types
from pygls.exceptions import JsonRpcContentModified
from pygls.lsp.client import LanguageClient

from .schema import NATIVE_TAGS

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Sequence
    from typing import Any

type NavigationResult = (
    types.Location | Sequence[types.Location] | Sequence[types.LocationLink] | None
)
type CompletionResult = Sequence[types.CompletionItem] | types.CompletionList | None

LOG = logging.getLogger(__name__)


class ClientProcess(Protocol):
    """Typed boundary for the framework's unannotated subprocess entry points."""

    async def start_io(self, cmd: str, *args: str, cwd: Path) -> None: ...

    async def stop(self) -> None: ...


class FeatureRegistry(Protocol):
    def feature(
        self,
        feature_name: str,
    ) -> Callable[[Callable[..., None]], object]: ...


@dataclass(frozen=True)
class Stamp:
    """A document version cannot distinguish two dependency snapshots."""

    generation: int
    backend: int

    def require_current(self, current: Stamp) -> None:
        if self != current:
            raise ValueError("Analysis was superseded; request the action again")


class BackendClient(LanguageClient):
    def __init__(self, died: Callable[[], None]) -> None:
        super().__init__("pysx-type-client", "0.1.0")
        self.died = died
        self.closing = False

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self._server

    async def server_exit(self, server: asyncio.subprocess.Process) -> None:
        if server.returncode is not None and not self.closing:
            self.died()


class TypeBackend:
    """One bounded Pyright process, using pygls for framing and request dispatch."""

    def __init__(
        self,
        publish: Callable[[types.PublishDiagnosticsParams], None],
        died: Callable[[], None],
    ) -> None:
        self.client = BackendClient(died)

        def diagnostics(params: types.PublishDiagnosticsParams) -> None:
            publish(params)

        cast("FeatureRegistry", self.client.protocol.fm).feature(
            types.TEXT_DOCUMENT_PUBLISH_DIAGNOSTICS
        )(diagnostics)

        def message(params: types.LogMessageParams) -> None:
            if params.type == types.MessageType.Error:
                LOG.error("Type backend: %s", params.message[:2000])

        cast("FeatureRegistry", self.client.protocol.fm).feature(types.WINDOW_LOG_MESSAGE)(message)

        def progress(_params: object) -> None:
            return None

        for method in ("pyright/beginProgress", "pyright/reportProgress", "pyright/endProgress"):
            cast("FeatureRegistry", self.client.protocol.fm).feature(method)(progress)

        self.stderr_task: asyncio.Task[None] | None = None
        self.starts = 0
        self.requests: set[asyncio.Task[object]] = set()
        self.query_uri = ""
        self.query_version = 0
        self.query_lock = asyncio.Lock()
        self.native_signatures: dict[str, types.SignatureHelp | None] = {}
        self.inspection_uri = ""
        self.inspection_version = 0
        self.inspection_name = "__pysx_query_" + uuid4().hex + ".py"

    @property
    def alive(self) -> bool:
        process = self.client.process

        return process is not None and process.returncode is None

    @property
    def pid(self) -> int | None:
        process = self.client.process

        return process.pid if process is not None else None

    async def start(self, root: Path, interpreter: str) -> None:
        self.query_uri = (root / "pysx_native_query.py").as_uri()
        node = shutil.which("node")

        if node is None:
            raise RuntimeError("Pyright requires Node.js in the language server environment")
        distribution = importlib.metadata.distribution("pyright")

        if distribution.version != "1.1.414":
            raise RuntimeError("Install pysx[lsp] to restore the supported Pyright version")
        entry = Path(str(distribution.locate_file("pyright/dist/langserver.index.js")))

        if not entry.is_file():
            raise RuntimeError("Pyright language server is missing; reinstall pysx[lsp]")
        await cast("ClientProcess", self.client).start_io(node, str(entry), "--stdio", cwd=root)
        self.starts += 1
        self.stderr_task = asyncio.create_task(self._drain_stderr())
        await asyncio.wait_for(
            self.client.initialize_async(
                types.InitializeParams(
                    capabilities=types.ClientCapabilities(),
                    process_id=os.getpid(),
                    root_uri=root.as_uri(),
                    workspace_folders=[types.WorkspaceFolder(root.as_uri(), root.name)],
                )
            ),
            15,
        )
        self.client.initialized(types.InitializedParams())
        self.client.workspace_did_change_configuration(
            types.DidChangeConfigurationParams(
                settings={
                    "python": {
                        "pythonPath": interpreter,
                        "analysis": {"diagnosticMode": "workspace"},
                    }
                }
            )
        )

    async def _drain_stderr(self) -> None:
        process = self.client.process

        if process is None or process.stderr is None:
            return

        while line := await process.stderr.readline():
            LOG.warning("Type backend: %s", line.decode(errors="replace").rstrip()[:2000])

    async def hover(self, uri: str, position: types.Position) -> types.Hover | None:
        return await self.request(
            self.client.text_document_hover_async(
                types.HoverParams(types.TextDocumentIdentifier(uri), position)
            )
        )

    async def definition(self, uri: str, position: types.Position) -> NavigationResult:
        return await self.request(
            self.client.text_document_definition_async(
                types.DefinitionParams(types.TextDocumentIdentifier(uri), position)
            )
        )

    async def native_signature(self, tag: str) -> types.SignatureHelp | None:
        if tag not in NATIVE_TAGS:
            return None

        async with self.query_lock:
            if tag in self.native_signatures:
                return self.native_signatures[tag]
            self.query_version += 1
            text = f"import pysx.native as native\nnative.{tag.capitalize()}()\n"

            if self.query_version == 1:
                self.client.text_document_did_open(
                    types.DidOpenTextDocumentParams(
                        types.TextDocumentItem(self.query_uri, "python", 1, text)
                    )
                )
            else:
                self.client.text_document_did_change(
                    types.DidChangeTextDocumentParams(
                        types.VersionedTextDocumentIdentifier(self.query_version, self.query_uri),
                        [types.TextDocumentContentChangeWholeDocument(text)],
                    )
                )
            result = await self.request(
                self.client.text_document_signature_help_async(
                    types.SignatureHelpParams(
                        types.TextDocumentIdentifier(self.query_uri),
                        types.Position(1, len("native.") + len(tag) + 1),
                    )
                )
            )
            self.native_signatures[tag] = result

            return result

    async def completion(self, uri: str, point: types.Position) -> CompletionResult:
        return await self.request(
            self.client.text_document_completion_async(
                types.CompletionParams(types.TextDocumentIdentifier(uri), point)
            )
        )

    async def signature(self, uri: str, point: types.Position) -> types.SignatureHelp | None:
        return await self.request(
            self.client.text_document_signature_help_async(
                types.SignatureHelpParams(types.TextDocumentIdentifier(uri), point)
            )
        )

    async def inspect(
        self,
        uri: str,
        text: str,
        point: types.Position,
    ) -> tuple[types.SignatureHelp | None, CompletionResult]:
        async with self.query_lock:
            self.inspection_version += 1

            if uri != self.inspection_uri:
                if self.inspection_uri:
                    self.client.text_document_did_close(
                        types.DidCloseTextDocumentParams(
                            types.TextDocumentIdentifier(self.inspection_uri)
                        )
                    )
                self.inspection_uri = uri
                self.client.text_document_did_open(
                    types.DidOpenTextDocumentParams(
                        types.TextDocumentItem(uri, "python", self.inspection_version, text)
                    )
                )
            else:
                self.client.text_document_did_change(
                    types.DidChangeTextDocumentParams(
                        types.VersionedTextDocumentIdentifier(self.inspection_version, uri),
                        [types.TextDocumentContentChangeWholeDocument(text)],
                    )
                )
            signature, completion = await asyncio.gather(
                self.signature(uri, point), self.completion(uri, point)
            )

            return signature, completion

    async def references(
        self,
        uri: str,
        position: types.Position,
        *,
        declaration: bool,
    ) -> Sequence[types.Location] | None:
        return await self.request(
            self.client.text_document_references_async(
                types.ReferenceParams(
                    context=types.ReferenceContext(declaration),
                    text_document=types.TextDocumentIdentifier(uri),
                    position=position,
                )
            )
        )

    async def request[T](self, operation: Coroutine[Any, Any, T]) -> T:
        # Shielding keeps cancellation of a public request local. The pinned backend
        # cannot safely process $/cancelRequest; its eventual result is superseded.
        if len(self.requests) >= 32:
            operation.close()

            raise RuntimeError("Type request limit reached; wait for current analysis")
        task = asyncio.create_task(operation)
        erased = cast("asyncio.Task[object]", task)
        self.requests.add(erased)

        def finished(done: asyncio.Task[object]) -> None:
            self.requests.discard(done)

            if not done.cancelled():
                done.exception()

        erased.add_done_callback(finished)

        try:
            return await asyncio.wait_for(asyncio.shield(task), 15)
        except TimeoutError as error:
            raise JsonRpcContentModified(
                "Type analysis is still running; retry the request"
            ) from error

    async def close(self) -> None:
        self.client.closing = True
        process = self.client.process

        try:
            if self.alive:
                try:
                    await asyncio.wait_for(self.client.shutdown_async(None), 2)
                    self.client.exit(None)

                    if process is not None:
                        await asyncio.wait_for(process.wait(), 2)
                except TimeoutError, BrokenPipeError, ConnectionError:
                    if process is not None and process.returncode is None:
                        process.kill()

            if process is not None:
                await process.wait()
            await cast("ClientProcess", self.client).stop()
            await asyncio.gather(*self.requests, return_exceptions=True)
        finally:
            if self.stderr_task is not None:
                await self.stderr_task
