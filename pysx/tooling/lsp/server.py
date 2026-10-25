"""Resident stdio lifecycle, coherent analysis scheduling and mapped type-service seam."""

from __future__ import annotations

import asyncio
import bisect
import logging
import os
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import unquote

from lsprotocol import types
from pygls.exceptions import JsonRpcContentModified, JsonRpcException
from pygls.lsp.server import LanguageServer
from pygls.protocol import LanguageServerProtocol, lsp_method
from pygls.workspace.position_codec import ServerTextPosition, ServerTextRange
from pygls.workspace.text_document import TextDocument

from pysx.compiler.compiler import component_binding_identity
from pysx.compiler.source_map import SourceSpan, utf16_offsets
from pysx.components.schema import NATIVE_TAGS, tag_info
from pysx.tooling.editor.model import MAX_BYTES, MAX_FILES, Buffer
from pysx.tooling.sources import read_source
from pysx.tooling.tools import ToolingError, resolve_ruff

from .backend import CompletionResult, Stamp, TypeBackend
from .edits import (
    Edit,
    RenameIndex,
    UnsupportedEditError,
    apply_edits,
    check_new_name,
    format_edits,
    import_sites,
    rename_edits,
    rename_target,
)
from .features import (
    SEMANTIC_TOKEN_MODIFIERS,
    SEMANTIC_TOKEN_TYPES,
    Context,
    classify,
    client_range,
    completions,
    document_symbols,
    folding_ranges,
    mapped_locations,
    selection_spans,
    semantic_tokens,
    signature,
    signature_target,
    speculative_query,
    template_calls,
)
from .workspace import Snapshot, SourceFile, WorkspaceIndex, offset, path_for_uri, position

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence

    from .features import TemplateCall

LOG = logging.getLogger(__name__)
TEMPLATE_FIX = "source.fixAll.pysx"
ORGANIZE_IMPORTS = "source.organizeImports.pysx"


class AnalysisUnavailable(JsonRpcException):
    CODE = -32001


class VersionedProtocol(LanguageServerProtocol):
    @lsp_method(types.INITIALIZE)
    def lsp_initialize(
        self, params: types.InitializeParams
    ) -> Generator[object, object, types.InitializeResult]:
        result = yield from super().lsp_initialize(params)
        options = params.initialization_options
        declared = cast("dict[str, object]", options) if isinstance(options, dict) else {}

        if declared.get("markupHighlighting") == "decorations":
            result.capabilities.semantic_tokens_provider = None
        selected = declared.get("pythonPath")

        # The client owns the selected environment per workspace folder, so the
        # pinned backend must follow it from the first request, not only after a
        # later configuration notification.
        if isinstance(selected, str) and selected:
            cast("ResidentServer", self._server).interpreter = selected

        return result

    def admit(self, uri: str, source: str) -> None:
        if len(source.encode()) > MAX_BYTES:
            raise ValueError("Open document exceeds the workspace source limit")
        session = cast("ResidentServer", self._server).session(uri)
        filename = str(path_for_uri(uri))
        size = sum(
            len(buffer["source"].encode())
            for name, buffer in session.buffers.items()
            if name != filename
        )

        if size + len(source.encode()) > MAX_BYTES:
            raise ValueError("Open buffers exceed the workspace source limit")

        if filename not in session.buffers and len(session.buffers) >= MAX_FILES:
            raise ValueError("Open document limit exceeded")

    @lsp_method(types.TEXT_DOCUMENT_DID_OPEN)
    def lsp_text_document__did_open(
        self,
        params: types.DidOpenTextDocumentParams,
    ) -> Generator[tuple[object, tuple[types.DidOpenTextDocumentParams], None], object]:
        self.admit(params.text_document.uri, params.text_document.text)
        yield from super().lsp_text_document__did_open(params)

    @lsp_method(types.TEXT_DOCUMENT_DID_CHANGE)
    def lsp_text_document__did_change(
        self,
        params: types.DidChangeTextDocumentParams,
    ) -> Generator[tuple[object, tuple[types.DidChangeTextDocumentParams], None], object]:
        if unquote(params.text_document.uri) not in self.workspace.text_documents:
            return
        document = self.workspace.get_text_document(params.text_document.uri)

        if document.version is not None and params.text_document.version <= document.version:
            return
        shadow = TextDocument(
            document.uri,
            source=document.source,
            version=document.version,
            position_codec=document.position_codec,
        )

        for change in params.content_changes:
            shadow.apply_change(change)
        self.admit(document.uri, shadow.source)
        yield from super().lsp_text_document__did_change(params)


class Session:
    def __init__(self, server: ResidentServer, root: Path) -> None:
        self.server = server
        self.index = WorkspaceIndex(root)
        self.buffers: dict[str, Buffer] = {}
        self.uris: dict[str, str] = {}
        self.generation = 0
        self.epoch = 0
        self.snapshot: Snapshot | None = None
        self.backend: TypeBackend | None = None
        self.sent: dict[str, int] = {}
        self.typed: dict[str, list[types.Diagnostic]] = {}
        self.event = asyncio.Event()
        self.ready = asyncio.Event()
        self.available = asyncio.Event()
        self.requested: set[str] = set()
        self.closed = False
        self.error: str | None = None
        self.starts = 0
        self.failures = 0
        self.backend_interpreter = ""
        self.rebuilds = 0
        self.changed: list[str] = []
        self.republished: set[str] = set()
        self.task = asyncio.create_task(self._run())

    @property
    def stamp(self) -> Stamp:
        return Stamp(self.generation, self.epoch)

    def invalidate(self) -> None:
        self.generation += 1
        self.server.protocol.notify(
            "pysx/analysisChanged",
            {"uris": list(self.uris.values()), "generation": self.generation},
        )
        self.republished.clear()
        self.changed = []
        self.ready.clear()
        self.available.clear()
        self.requested.update(self.buffers)
        self.event.set()

    def backend_died(self) -> None:
        if not self.closed:
            self.failures += 1
            self.error = "Type backend exited; restarting analysis"
            self.invalidate()

    def publish(self, filename: str) -> None:
        item = self.snapshot.file(filename) if self.snapshot is not None else None

        if item is None or filename not in self.buffers or self.snapshot is None:
            return

        if self.snapshot.stamp != self.stamp:
            return
        uri = self.uris[filename]
        document = self.server.workspace.get_text_document(uri)
        diagnostics = self.index.static_diagnostics(filename) + self.typed.get(filename, [])
        converted: list[types.Diagnostic] = []

        for finding in diagnostics:
            # Checker/source maps use UTF-16; pygls owns negotiated client positions.
            start = offset(item.source, finding.range.start)
            end = offset(item.source, finding.range.end)
            prefix = item.source[:start]
            ending = item.source[:end]
            converted.append(
                types.Diagnostic(
                    document.range_to_client_units(
                        ServerTextRange(
                            ServerTextPosition(prefix.count("\n"), len(prefix.rsplit("\n", 1)[-1])),
                            ServerTextPosition(ending.count("\n"), len(ending.rsplit("\n", 1)[-1])),
                        )
                    ),
                    finding.message,
                    severity=finding.severity,
                    code=finding.code,
                    source="pysx",
                )
            )
        self.server.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(uri, converted, item.version)
        )

    def accept(self, params: types.PublishDiagnosticsParams) -> None:
        if self.closed or self.snapshot is None or self.snapshot.stamp != self.stamp:
            return

        projected = path_for_uri(params.uri)
        item = next(
            (
                item
                for item in self.snapshot.files
                if self.index.projected / item.relative == projected
            ),
            None,
        )

        if item is None:
            return
        self.republished.add(item.relative)

        if params.version is None or self.sent.get(params.uri) != params.version:
            return
        mapped: list[types.Diagnostic] = []

        for finding in params.diagnostics:
            if finding.code in {"reportUnusedImport", "reportUnusedVariable"}:
                continue
            function_handoff = (
                finding.code == "reportUnusedFunction"
                and self.index.settings.get("reportUnusedFunction") is False
            )

            if not function_handoff and not item.owns_range(finding.range):
                continue
            target = item.original_range(finding.range)

            if target is not None:
                mapped.append(
                    types.Diagnostic(
                        target,
                        finding.message.replace(self.index.revision + ".", ""),
                        severity=finding.severity,
                        code=finding.code,
                    )
                )
        self.typed[item.filename] = mapped
        self.publish(item.filename)

    async def _backend(self, *, reconfigure: bool = False) -> TypeBackend:
        if (
            not reconfigure
            and self.backend is not None
            and self.backend.alive
            and self.backend_interpreter == self.server.interpreter
        ):
            return self.backend

        if self.failures >= 3:
            raise RuntimeError("Type backend restart limit reached; restart the language server")

        if self.backend is not None:
            await self.backend.close()
        self.epoch += 1
        self.backend = TypeBackend(self.accept, self.backend_died)
        self.index.write_config()
        await self.backend.start(self.index.directory, self.server.interpreter)
        self.starts += 1
        self.backend_interpreter = self.server.interpreter
        self.sent.clear()
        self.typed.clear()

        return self.backend

    async def _run(self) -> None:
        try:
            await self._drive()
        finally:
            try:
                if self.backend is not None:
                    await self.backend.close()
            finally:
                self.index.close()

    async def _drive(self) -> None:
        while not self.closed:
            await self.event.wait()
            await asyncio.sleep(0.08)
            self.event.clear()

            if self.closed:
                break

            try:
                generation = self.generation
                buffers = dict(self.buffers)
                baseline = self.snapshot
                remaining = await asyncio.to_thread(self.index.files, buffers)
                all_files = set(remaining)
                collected: dict[str, SourceFile] = {}

                while remaining and not self.closed and generation == self.generation:
                    # Finish one bounded chunk before admitting newer foreground
                    # work. Only this driver mutates the compiler/index cache.
                    foreground = all_files.intersection(self.requested)
                    selected = foreground or set(sorted(remaining)[:8])
                    self.requested.difference_update(selected)

                    while True:
                        part = await asyncio.to_thread(
                            self.index.build, buffers, self.stamp, selected=selected
                        )

                        if self.closed or generation != self.generation:
                            break
                        dependencies: set[str] = (
                            {
                                dependency
                                for name in selected
                                for dependency in self.index.dependencies(name)
                            }
                            if foreground
                            else set()
                        )
                        missing = dependencies.intersection(remaining) - selected

                        if not missing:
                            break
                        selected |= missing

                    if self.closed or generation != self.generation:
                        break
                    collected.update((item.filename, item) for item in part.files)
                    remaining.difference_update(selected)

                    if foreground:
                        await self._synchronize(
                            replace(part, files=tuple(collected.values())), baseline, complete=False
                        )
                        self.requested.difference_update(collected)
                    # Yield between chunks so edits and requests can reprioritize
                    # the next chunk without racing a mutable background index.
                    await asyncio.sleep(0)

                if self.closed:
                    break

                if generation != self.generation:
                    self.event.set()

                    continue
                snapshot = await asyncio.to_thread(self.index.build, buffers, self.stamp)

                if generation != self.generation:
                    self.event.set()

                    continue
                self.rebuilds += 1
                await self._synchronize(snapshot, baseline, complete=True)
            except Exception as error:
                self.error = str(error)
                LOG.exception("Language analysis failed")
                self.server.window_log_message(
                    types.LogMessageParams(
                        types.MessageType.Error,
                        f"PYSX analysis unavailable: {error}. "
                        "Install pysx[lsp] or restart the server.",
                    )
                )
                self.ready.set()
                self.available.set()

    async def _synchronize(
        self, snapshot: Snapshot, baseline: Snapshot | None, *, complete: bool
    ) -> None:
        generation = snapshot.stamp.generation
        affected = self.index.affected(baseline, partial=not complete)
        disk_events: list[types.FileEvent] = []
        current_paths = {self.index.projected / item.relative for item in snapshot.files}

        for item in snapshot.files:
            target = self.index.projected / item.relative

            if not target.is_file() or item.filename in affected:
                kind = (
                    types.FileChangeType.Changed
                    if target.is_file()
                    else types.FileChangeType.Created
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item.text, encoding="utf-8")
                disk_events.append(types.FileEvent(target.as_uri(), kind))

        if complete and baseline is not None:
            for item in baseline.files:
                target = self.index.projected / item.relative

                if target not in current_paths:
                    target.unlink(missing_ok=True)
                    disk_events.append(
                        types.FileEvent(target.as_uri(), types.FileChangeType.Deleted)
                    )
        config_changed = self.index.write_config()
        backend = await self._backend(reconfigure=config_changed)

        if generation != self.generation:
            self.event.set()

            return
        stamp = self.stamp
        self.snapshot = replace(snapshot, stamp=stamp)
        active: set[str] = set()

        for item in snapshot.files:
            uri = (self.index.projected / item.relative).as_uri()

            if item.filename in affected and item.relative not in self.changed:
                self.changed.append(item.relative)

            if item.filename not in self.buffers:
                continue
            active.add(uri)

            if uri not in self.sent:
                backend.client.text_document_did_open(
                    types.DidOpenTextDocumentParams(
                        types.TextDocumentItem(uri, "python", stamp.generation, item.text)
                    )
                )
            elif item.filename in affected and self.sent[uri] != stamp.generation:
                backend.client.text_document_did_change(
                    types.DidChangeTextDocumentParams(
                        types.VersionedTextDocumentIdentifier(stamp.generation, uri),
                        [types.TextDocumentContentChangeWholeDocument(item.text)],
                    )
                )
            else:
                continue
            self.sent[uri] = stamp.generation
            self.typed.pop(item.filename, None)

        if complete:
            for uri in self.sent.keys() - active:
                backend.client.text_document_did_close(
                    types.DidCloseTextDocumentParams(types.TextDocumentIdentifier(uri))
                )
                del self.sent[uri]

        if disk_events:
            backend.client.workspace_did_change_watched_files(
                types.DidChangeWatchedFilesParams(disk_events)
            )
        self.error = None
        self.available.set()

        if complete:
            self.ready.set()

        for filename in self.buffers:
            self.publish(filename)

    async def current(self, filename: str | None = None, *, complete: bool = False) -> Snapshot:
        if filename is not None and (
            self.snapshot is None
            or self.snapshot.stamp != self.stamp
            or self.snapshot.file(filename) is None
        ):
            self.requested.add(filename)

        try:
            if complete:
                await asyncio.wait_for(self.ready.wait(), 2)
            else:
                async with asyncio.timeout(20):
                    while True:
                        self.available.clear()

                        if self.error is not None or (
                            self.snapshot is not None
                            and self.snapshot.stamp == self.stamp
                            and (filename is None or self.snapshot.file(filename) is not None)
                        ):
                            break
                        await self.available.wait()
        except TimeoutError as error:
            raise JsonRpcContentModified(
                "Analysis is not ready; retry after workspace synchronization"
            ) from error

        if self.error is not None or self.snapshot is None:
            raise AnalysisUnavailable(
                self.error or "No current workspace analysis; restart the server"
            )

        if self.snapshot.stamp != self.stamp:
            raise JsonRpcContentModified("Analysis was superseded; retry the request")

        return self.snapshot

    async def close(self) -> None:
        self.closed = True
        self.event.set()
        await self.task
        self.buffers.clear()
        self.uris.clear()
        self.snapshot = None
        self.typed.clear()
        self.sent.clear()


class ResidentServer(LanguageServer):
    sessions: dict[Path, Session]
    interpreter: str

    def session(self, uri: str) -> Session:
        path = path_for_uri(uri)

        if not uri.startswith("file:") or path.suffix != ".py":
            raise ValueError("Only local Python source documents are supported")
        folders = [path_for_uri(folder.uri) for folder in self.workspace.folders.values()]

        if self.workspace.root_path is not None:
            folders.append(Path(self.workspace.root_path).resolve())
        roots = [root for root in folders if path.is_relative_to(root)]
        root = max(roots, key=lambda root: len(root.parts)) if roots else path.parent

        if root not in self.sessions:
            if len(self.sessions) >= 8:
                raise ValueError("At most eight analysis workspaces may be open")
            self.sessions[root] = Session(self, root)

        return self.sessions[root]


def create_server() -> ResidentServer:
    server = ResidentServer("pysx", "0.1.0", protocol_cls=VersionedProtocol)
    server.sessions = {}
    server.interpreter = sys.executable

    @server.feature(types.TEXT_DOCUMENT_DID_OPEN)
    def opened(params: types.DidOpenTextDocumentParams) -> None:
        document = params.text_document
        session = server.session(document.uri)
        filename = str(path_for_uri(document.uri))

        if len(session.buffers) >= MAX_FILES or len(document.text.encode()) > MAX_BYTES:
            raise ValueError("Open document limit exceeded")
        session.buffers[filename] = Buffer(source=document.text, version=document.version)
        session.uris[filename] = document.uri
        session.invalidate()

    @server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
    def changed(params: types.DidChangeTextDocumentParams) -> None:
        session = server.session(params.text_document.uri)
        filename = str(path_for_uri(params.text_document.uri))
        document = server.workspace.get_text_document(params.text_document.uri)

        if filename not in session.buffers:
            return

        if len(document.source.encode()) > MAX_BYTES:
            raise ValueError("Open document limit exceeded")
        session.buffers[filename] = Buffer(
            source=document.source,
            version=params.text_document.version,
        )
        session.invalidate()

    @server.feature(types.TEXT_DOCUMENT_DID_SAVE)
    def saved(params: types.DidSaveTextDocumentParams) -> None:
        server.session(params.text_document.uri).invalidate()

    @server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
    async def closed(params: types.DidCloseTextDocumentParams) -> None:
        session = server.session(params.text_document.uri)
        filename = str(path_for_uri(params.text_document.uri))
        session.buffers.pop(filename, None)
        session.uris.pop(filename, None)
        session.typed.pop(filename, None)
        server.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(
                params.text_document.uri,
                [],
            )
        )

        if not session.buffers:
            del server.sessions[session.index.root]
            await session.close()
        else:
            session.invalidate()

    async def owned(
        uri: str,
        point: types.Position,
    ) -> tuple[Session, Snapshot, SourceFile, Context] | None:
        session = server.session(uri)
        issued_generation = session.generation
        snapshot = await session.current(str(path_for_uri(uri)))

        if issued_generation != session.generation:
            raise JsonRpcContentModified("Analysis was superseded; retry the request")
        item = snapshot.file(str(path_for_uri(uri)))

        if item is None:
            return None
        document = server.workspace.get_text_document(uri)
        at = document.offset_at_position(point)
        context = classify(
            item.source,
            at,
            item.filename,
            session.index.root,
            {item.filename: item.source for item in snapshot.files},
        )

        if context is None or context.kind in {"text", "value", "style"}:
            return None

        return session, snapshot, item, context

    async def semantic_help(
        session: Session,
        snapshot: Snapshot,
        item: SourceFile,
        context: Context,
    ) -> tuple[types.SignatureHelp | None, CompletionResult]:
        backend = session.backend

        if backend is None:
            return None, None
        target = signature_target(item, context)

        if target is not None:
            uri = (session.index.projected / item.relative).as_uri()
            result, candidates = await asyncio.gather(
                backend.signature(uri, target), backend.completion(uri, target)
            )
        else:
            query = speculative_query(context, snapshot, session.index.revision)

            if query is None:
                return None, None
            text, target = query
            uri = (
                session.index.projected / Path(item.relative).parent / backend.inspection_name
            ).as_uri()
            result, candidates = await backend.inspect(uri, text, target)

        if snapshot.stamp != session.stamp:
            raise JsonRpcContentModified("Analysis was superseded; retry the request")

        return result, candidates

    @server.feature(
        types.TEXT_DOCUMENT_COMPLETION, types.CompletionOptions(trigger_characters=["(", ","])
    )
    async def complete(params: types.CompletionParams) -> list[types.CompletionItem]:
        selected = await owned(params.text_document.uri, params.position)

        if selected is None:
            return []
        session, snapshot, item, context = selected
        document = server.workspace.get_text_document(params.text_document.uri)
        result = completions(context)

        if context.kind == "attribute" and context.native is None:
            _, candidates = await semantic_help(session, snapshot, item, context)
            candidates = (
                candidates.items if isinstance(candidates, types.CompletionList) else candidates
            )
            names = {candidate.label for candidate in result}

            for candidate in candidates or ():
                name = candidate.label.removesuffix("=")

                if (
                    candidate.label.endswith("=")
                    and name.isidentifier()
                    and name.startswith(context.prefix)
                    and name not in names
                ):
                    result.append(
                        types.CompletionItem(name, kind=types.CompletionItemKind.Property)
                    )
                    names.add(name)

        for candidate in result:
            candidate.text_edit = types.TextEdit(
                client_range(document, item.source, context.span), str(candidate.label)
            )

        return result

    @server.feature(
        types.TEXT_DOCUMENT_SIGNATURE_HELP,
        types.SignatureHelpOptions(trigger_characters=["(", ","]),
    )
    async def help_at(params: types.SignatureHelpParams) -> types.SignatureHelp | None:
        selected = await owned(params.text_document.uri, params.position)

        if selected is None:
            return None
        session, snapshot, item, context = selected

        if context.kind != "attribute":
            return None
        native = context.native
        result = (
            await session.backend.native_signature(native)
            if native is not None and session.backend is not None
            else (await semantic_help(session, snapshot, item, context))[0] or signature(context)
        )

        if snapshot.stamp != session.stamp:
            raise JsonRpcContentModified("Analysis was superseded; retry the request")

        return result

    @server.feature(types.TEXT_DOCUMENT_HOVER)
    async def hover(params: types.HoverParams) -> types.Hover | None:
        selected = await owned(params.text_document.uri, params.position)

        if selected is None:
            return None
        session, snapshot, item, context = selected
        document = server.workspace.get_text_document(params.text_document.uri)

        if context.kind == "tag" and context.word in NATIVE_TAGS:
            info = tag_info(context.word)
            assert info is not None
            value = f"`<{info.name}>` — native {info.namespace.upper()} element.\n\n"
            value += "Attributes: " + ", ".join(sorted(info.attributes))

            return types.Hover(
                types.MarkupContent(types.MarkupKind.Markdown, value),
                client_range(document, item.source, context.span),
            )
        target = item.generated_position(
            position(item.source, document.offset_at_position(params.position))
        )

        if target is None or session.backend is None:
            return (
                types.Hover(
                    types.MarkupContent(
                        types.MarkupKind.Markdown,
                        f"Static signature unavailable for dynamic component `{context.word}`.",
                    ),
                    client_range(document, item.source, context.span),
                )
                if context.kind == "tag" and context.word
                else None
            )
        result = await session.backend.hover(
            (session.index.projected / item.relative).as_uri(),
            target,
        )

        if snapshot.stamp != session.stamp:
            raise JsonRpcContentModified("Analysis was superseded; retry the request")

        if result is not None:
            result.range = client_range(document, item.source, context.span)

            if isinstance(result.contents, types.MarkupContent):
                result.contents.value = result.contents.value.replace(
                    session.index.revision + ".", ""
                )
            elif isinstance(result.contents, str):
                result.contents = result.contents.replace(session.index.revision + ".", "")

        return result

    def translated(
        locations: list[types.Location],
        snapshot: Snapshot,
        session: Session,
        uri: str,
    ) -> list[types.Location]:
        result: list[types.Location] = []
        document = server.workspace.get_text_document(uri)

        for location in locations:
            path = path_for_uri(location.uri)
            item = snapshot.file(str(path))

            try:
                source = item.source if item is not None else read_source(path)
                shadow = TextDocument(
                    location.uri, source=source, position_codec=document.position_codec
                )
                span = SourceSpan(
                    offset(source, location.range.start), offset(source, location.range.end)
                )
                result.append(
                    types.Location(
                        session.uris.get(str(path), location.uri),
                        client_range(shadow, source, span),
                    )
                )
            except OSError, ValueError, UnicodeError:
                continue

        return result

    @server.feature(types.TEXT_DOCUMENT_DEFINITION)
    async def definition(params: types.DefinitionParams) -> list[types.Location]:
        selected = await owned(params.text_document.uri, params.position)

        if selected is None:
            return []
        session, snapshot, item, _ = selected
        document = server.workspace.get_text_document(params.text_document.uri)
        target = item.generated_position(
            position(item.source, document.offset_at_position(params.position))
        )

        if target is None or session.backend is None:
            return []
        result = await session.backend.definition(
            (session.index.projected / item.relative).as_uri(), target
        )

        if snapshot.stamp != session.stamp:
            raise JsonRpcContentModified("Analysis was superseded; retry the request")

        return translated(
            mapped_locations(snapshot, session.index.projected, result, expand=False),
            snapshot,
            session,
            params.text_document.uri,
        )

    @server.feature(types.TEXT_DOCUMENT_REFERENCES)
    async def references(params: types.ReferenceParams) -> list[types.Location]:
        selected = await owned(params.text_document.uri, params.position)

        if selected is None:
            return []
        session, snapshot, item, _ = selected
        document = server.workspace.get_text_document(params.text_document.uri)
        target = item.generated_position(
            position(item.source, document.offset_at_position(params.position))
        )

        if target is None or session.backend is None:
            return []
        result = await session.backend.references(
            (session.index.projected / item.relative).as_uri(),
            target,
            declaration=params.context.include_declaration,
        )

        if snapshot.stamp != session.stamp:
            raise JsonRpcContentModified("Analysis was superseded; retry the request")

        at = utf16_offsets(item.source)[document.offset_at_position(params.position)]
        symbol = next((site.symbol for site in item.sites if site.start <= at < site.end), None)

        return translated(
            mapped_locations(snapshot, session.index.projected, result, expand=True, symbol=symbol),
            snapshot,
            session,
            params.text_document.uri,
        )

    async def structure(uri: str) -> tuple[Session, SourceFile, list[TemplateCall]] | None:
        session = server.session(uri)
        filename = str(path_for_uri(uri))
        buffer = session.buffers.get(filename)

        if buffer is None:
            return None
        source = buffer["source"]
        item = SourceFile(filename, source, "", buffer["version"], "", (), False, (), (), ())
        calls = template_calls(
            item.source,
            item.filename,
            session.index.root,
            {name: entry["source"] for name, entry in session.buffers.items()},
        )

        return session, item, calls

    @server.feature(
        types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
        types.SemanticTokensLegend(
            token_types=list(SEMANTIC_TOKEN_TYPES),
            token_modifiers=list(SEMANTIC_TOKEN_MODIFIERS),
        ),
    )
    async def tokens(params: types.SemanticTokensParams) -> types.SemanticTokens:
        selected = await structure(params.text_document.uri)

        if selected is None:
            return types.SemanticTokens(data=[])
        _session, item, calls = selected
        document = server.workspace.get_text_document(params.text_document.uri)
        data: list[int] = []
        line = 0
        character = 0

        for span, kind, modifiers in semantic_tokens(calls):
            where = client_range(document, item.source, span)

            if where.start.line != where.end.line:
                continue
            delta_line = where.start.line - line
            delta_start = where.start.character - (character if delta_line == 0 else 0)
            data.extend(
                (
                    delta_line,
                    delta_start,
                    where.end.character - where.start.character,
                    SEMANTIC_TOKEN_TYPES.index(kind),
                    sum(1 << SEMANTIC_TOKEN_MODIFIERS.index(name) for name in modifiers),
                )
            )
            line, character = where.start.line, where.start.character

        return types.SemanticTokens(data=data)

    @server.feature("pysx/markupTokens")
    async def markup_tokens(_ls: ResidentServer, params: object) -> dict[str, object]:
        uri = str(getattr(getattr(params, "textDocument", None), "uri", ""))
        selected = await structure(uri)
        document = server.workspace.get_text_document(uri)

        if selected is None:
            return {"version": document.version, "generation": 0, "tokens": []}
        session, item, calls = selected
        # Direct pysx(...) literal arguments already have TextMate scopes. Only
        # decorate static aliases/shared literals that the injection cannot see.
        uncovered: list[TemplateCall] = []

        for call in calls:
            prefix = item.source[call.call_span.start : call.source.span.start]

            if call.call_span.start <= call.source.span.start < call.call_span.end:
                prefix = re.sub(r"#[^\n]*(?:\n|$)", "", prefix)

                if re.match(r"(?:[A-Za-z_]\w*\s*\.\s*)?pysx\s*\(\s*[rRtT]*$", prefix):
                    continue
            uncovered.append(call)

        return {
            "version": item.version,
            "generation": session.generation,
            "tokens": [
                {
                    "range": client_range(document, item.source, span),
                    "kind": kind,
                    "modifiers": list(modifiers),
                }
                for span, kind, modifiers in semantic_tokens(uncovered)
            ],
        }

    @server.feature(types.TEXT_DOCUMENT_FOLDING_RANGE)
    async def folding(params: types.FoldingRangeParams) -> list[types.FoldingRange]:
        selected = await structure(params.text_document.uri)

        if selected is None:
            return []
        _session, item, calls = selected

        return [
            types.FoldingRange(start, end, kind=types.FoldingRangeKind.Region)
            for start, end in folding_ranges(calls, item.source)
        ]

    @server.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
    async def symbols(params: types.DocumentSymbolParams) -> list[types.DocumentSymbol]:
        selected = await structure(params.text_document.uri)

        if selected is None:
            return []
        _session, item, calls = selected
        document = server.workspace.get_text_document(params.text_document.uri)

        def convert(
            entries: list[tuple[str, str, SourceSpan, SourceSpan, list[object]]],
        ) -> list[types.DocumentSymbol]:
            return [
                types.DocumentSymbol(
                    name,
                    types.SymbolKind.Field if kind == "element" else types.SymbolKind.Operator,
                    client_range(document, item.source, span),
                    client_range(document, item.source, selection),
                    children=convert(
                        cast(
                            "list[tuple[str, str, SourceSpan, SourceSpan, list[object]]]",
                            children,
                        )
                    ),
                )
                for name, kind, span, selection, children in entries
            ]

        return convert(document_symbols(calls))

    @server.feature(types.TEXT_DOCUMENT_SELECTION_RANGE)
    async def selection(params: types.SelectionRangeParams) -> list[types.SelectionRange]:
        selected = await structure(params.text_document.uri)

        if selected is None:
            return []
        _session, item, calls = selected
        document = server.workspace.get_text_document(params.text_document.uri)
        result: list[types.SelectionRange] = []

        for point in params.positions:
            spans = selection_spans(calls, document.offset_at_position(point))
            current: types.SelectionRange | None = None

            for span in reversed(spans):
                current = types.SelectionRange(
                    client_range(document, item.source, span), parent=current
                )
            result.append(
                current if current is not None else types.SelectionRange(types.Range(point, point))
            )

        return result

    def as_edits(
        document: TextDocument, source: str, edits: Sequence[Edit]
    ) -> list[types.TextEdit]:
        return [
            types.TextEdit(client_range(document, source, edit.span), edit.text) for edit in edits
        ]

    def template_edits(item: SourceFile, calls: Sequence[TemplateCall]) -> list[Edit]:
        edits = [edit for call in calls for edit in format_edits(call)]
        apply_edits(item.source, edits)

        return edits

    @server.feature(types.TEXT_DOCUMENT_FORMATTING)
    async def formatting(params: types.DocumentFormattingParams) -> list[types.TextEdit]:
        selected = await structure(params.text_document.uri)

        if selected is None:
            return []
        _session, item, calls = selected
        document = server.workspace.get_text_document(params.text_document.uri)

        return as_edits(document, item.source, template_edits(item, calls))

    @server.feature(types.TEXT_DOCUMENT_RANGE_FORMATTING)
    async def range_formatting(
        params: types.DocumentRangeFormattingParams,
    ) -> list[types.TextEdit]:
        selected = await structure(params.text_document.uri)

        if selected is None:
            return []
        _session, item, calls = selected
        document = server.workspace.get_text_document(params.text_document.uri)
        left = document.offset_at_position(params.range.start)
        right = document.offset_at_position(params.range.end)
        inside = [
            edit
            for edit in template_edits(item, calls)
            if left <= edit.span.start and edit.span.end <= right
        ]

        return as_edits(document, item.source, inside)

    @server.feature(types.TEXT_DOCUMENT_PREPARE_RENAME)
    async def prepare_rename(
        params: types.PrepareRenameParams,
    ) -> types.PrepareRenameResult | None:
        selected = await owned(params.text_document.uri, params.position)

        if selected is None:
            return None
        _session, _snapshot, item, context = selected
        document = server.workspace.get_text_document(params.text_document.uri)

        try:
            name, span = rename_target(context)
        except UnsupportedEditError:
            return None

        return types.PrepareRenamePlaceholder(
            client_range(document, item.source, span), placeholder=name
        )

    @server.feature(types.TEXT_DOCUMENT_RENAME)
    async def rename(params: types.RenameParams) -> types.WorkspaceEdit:
        selected = await owned(params.text_document.uri, params.position)

        if selected is None:
            raise UnsupportedEditError("Place the cursor on a component tag to rename it")
        session, snapshot, item, context = selected
        selected_stamp = snapshot.stamp
        snapshot = await session.current(complete=True)

        if selected_stamp != snapshot.stamp:
            raise JsonRpcContentModified("Analysis was superseded; retry the request")
        name, _span = rename_target(context)
        check_new_name(params.new_name)
        symbol = component_binding_identity(name, context.scope, context.resolver, item.filename)

        if symbol is None or symbol.startswith("library:"):
            raise UnsupportedEditError(
                f"The binding of {name!r} could not be resolved; rename was not applied"
            )

        if symbol.rsplit(":", 1)[-1] != name:
            raise UnsupportedEditError(
                "Renaming a genuine import alias is not supported; rename its original declaration"
            )
        document = server.workspace.get_text_document(params.text_document.uri)
        target = item.generated_position(
            position(item.source, document.offset_at_position(params.position))
        )

        if target is None or session.backend is None:
            raise UnsupportedEditError(
                f"Analysis is not ready, so {name!r} cannot be renamed safely"
            )
        projected = (session.index.projected / item.relative).as_uri()
        # The pinned backend omits the owning declaration from a cross-file
        # reference sweep, and renaming uses without it is exactly what the
        # edit contract forbids, so the declaration is queried separately.
        found, declared = await asyncio.gather(
            session.backend.references(projected, target, declaration=True),
            session.backend.definition(projected, target),
        )

        if snapshot.stamp != session.stamp:
            raise JsonRpcContentModified("Analysis was superseded; retry the request")
        owner = mapped_locations(snapshot, session.index.projected, declared, expand=False)

        if not owner:
            raise UnsupportedEditError(
                f"The declaration of {name!r} could not be located; rename was not applied"
            )
        locations = owner + mapped_locations(
            snapshot, session.index.projected, found, expand=True, symbol=symbol
        )
        sources = {entry.filename: entry.source for entry in snapshot.files}
        indexes = {
            filename: RenameIndex(source, filename, session.index.root, sources)
            for filename, source in sources.items()
        }
        sites: dict[str, list[SourceSpan]] = {}

        for location in locations:
            filename = str(path_for_uri(location.uri))
            source = sources.get(filename)

            if source is None:
                raise UnsupportedEditError(
                    "A reference lies outside the analyzed workspace; rename was not applied"
                )
            span = SourceSpan(
                offset(source, location.range.start), offset(source, location.range.end)
            )

            if source[span.start : span.end] != name:
                entry = snapshot.file(filename)
                units = utf16_offsets(source)
                template_alias = entry is not None and any(
                    site.start == units[span.start]
                    and site.end == units[span.end]
                    and site.symbol == symbol
                    for site in entry.sites
                )

                if template_alias or indexes[filename].reference_identity(span) == symbol:
                    continue

                raise UnsupportedEditError(
                    "A reference could not be verified as the selected binding; "
                    "rename was not applied"
                )
            sites.setdefault(filename, []).append(span)

        for filename, index in indexes.items():
            imported = import_sites(index, name, symbol)

            if imported:
                sites.setdefault(filename, []).extend(imported)
        edits = rename_edits(name, sources, sites, params.new_name)

        return types.WorkspaceEdit(
            changes={
                session.uris.get(filename, Path(filename).as_uri()): as_edits(
                    TextDocument(
                        Path(filename).as_uri(),
                        source=sources[filename],
                        position_codec=document.position_codec,
                    ),
                    sources[filename],
                    file_edits,
                )
                for filename, file_edits in edits.items()
            }
        )

    def import_action(uri: str) -> types.CodeAction | None:
        """Offer the removal only against complete, current, coherent analysis."""
        session = server.session(uri)
        filename = str(path_for_uri(uri))
        document = server.workspace.get_text_document(uri)
        buffer = session.buffers.get(filename)

        snapshot = session.snapshot

        # Save participants must not wait for a workspace sweep. Only use a
        # completed current result; otherwise leave imports untouched this save.
        if (
            buffer is None
            or not session.ready.is_set()
            or session.error is not None
            or snapshot is None
            or snapshot.stamp != session.stamp
        ):
            return None
        item = snapshot.file(filename)

        if item is None or not item.complete or not item.imports:
            return None

        if item.version != buffer["version"] or item.source != buffer["source"]:
            return None
        units = utf16_offsets(item.source)
        spans = [
            Edit(SourceSpan(bisect.bisect_left(units, start), bisect.bisect_left(units, end)), text)
            for start, end, text in item.imports
        ]

        return types.CodeAction(
            "PYSX: remove unused imports",
            kind=cast("types.CodeActionKind", ORGANIZE_IMPORTS),
            edit=types.WorkspaceEdit(changes={uri: as_edits(document, item.source, spans)}),
        )

    @server.feature(
        types.TEXT_DOCUMENT_CODE_ACTION,
        types.CodeActionOptions(
            code_action_kinds=[
                cast("types.CodeActionKind", TEMPLATE_FIX),
                cast("types.CodeActionKind", ORGANIZE_IMPORTS),
            ]
        ),
    )
    async def code_actions(params: types.CodeActionParams) -> list[types.CodeAction]:
        requested = params.context.only
        actions: list[types.CodeAction] = []
        selected = (
            await structure(params.text_document.uri)
            if requested is None or TEMPLATE_FIX in requested
            else None
        )

        if selected is not None and (requested is None or TEMPLATE_FIX in requested):
            _session, item, calls = selected
            document = server.workspace.get_text_document(params.text_document.uri)
            edits = template_edits(item, calls)

            if edits:
                actions.append(
                    types.CodeAction(
                        "PYSX: normalize template indentation",
                        kind=cast("types.CodeActionKind", TEMPLATE_FIX),
                        edit=types.WorkspaceEdit(
                            changes={
                                params.text_document.uri: as_edits(document, item.source, edits)
                            }
                        ),
                    )
                )

        if requested is None or ORGANIZE_IMPORTS in requested:
            organize = import_action(params.text_document.uri)

            if organize is not None:
                actions.append(organize)

        return actions

    @server.feature(types.WORKSPACE_DID_CHANGE_WATCHED_FILES)
    def watched(_params: types.DidChangeWatchedFilesParams) -> None:
        for session in server.sessions.values():
            session.invalidate()

    @server.feature(types.WORKSPACE_DID_CHANGE_CONFIGURATION)
    def configured(params: types.DidChangeConfigurationParams) -> None:
        settings = cast("object", params.settings)
        interpreter = (
            cast("dict[str, object]", settings).get("pythonPath")
            if isinstance(settings, dict)
            else None
        )

        if isinstance(interpreter, str):
            server.interpreter = interpreter

        for session in server.sessions.values():
            session.invalidate()

    def requested_uri(params: object) -> str | None:
        """Custom request params arrive as a dict or a generated attribute object."""
        value = (
            cast("dict[str, object]", params).get("uri")
            if isinstance(params, dict)
            else getattr(params, "uri", None)
        )

        return value if isinstance(value, str) else None

    @server.feature("pysx/readiness")
    async def readiness(_ls: ResidentServer, params: object) -> dict[str, object]:
        """What setup needs: is analysis coherent, and is each silenced rule replaced?"""
        uri = requested_uri(params)
        session = server.session(uri) if uri is not None else None
        ready = session is not None and session.ready.is_set()

        try:
            resolve_ruff(Path(server.interpreter))
            ruff = True
        except ToolingError:
            ruff = False

        return {
            "root": str(session.index.root) if session is not None else None,
            "ready": ready,
            "error": session.error if session is not None else "No analysis for this document",
            "coverage": {
                "ruff": ruff,
                "types": session is not None and session.backend is not None,
                # The replacement action is implemented here, so setup may only
                # disable the competing actions when this server is answering.
                "organizeImports": True,
            },
        }

    @server.feature("pysx/refresh")
    async def refresh(_ls: ResidentServer, params: object) -> dict[str, object]:
        uri = requested_uri(params)

        if uri is None:
            raise ValueError("pysx/refresh requires a document uri")
        session = server.session(uri)
        session.invalidate()
        await session.current(str(path_for_uri(uri)))

        return {"root": str(session.index.root), "generation": session.generation}

    @server.feature("pysx/status")
    def status(_ls: ResidentServer, _params: object) -> dict[str, object]:
        return {
            "processId": os.getpid(),
            "interpreter": server.interpreter,
            "workspaces": [
                {
                    "root": str(root),
                    "generation": session.generation,
                    "epoch": session.epoch,
                    "pid": session.backend.pid if session.backend is not None else None,
                    "starts": session.starts,
                    "rebuilds": session.rebuilds,
                    "changed": session.changed,
                    "republished": sorted(session.republished),
                    "openDocuments": len(session.buffers),
                    "cachedDocuments": len(session.sent),
                    "ready": session.ready.is_set(),
                    "error": session.error,
                }
                for root, session in server.sessions.items()
            ],
        }

    @server.feature(types.SHUTDOWN)
    async def shutdown(_ls: ResidentServer, _params: object) -> None:
        await asyncio.gather(*(session.close() for session in server.sessions.values()))
        server.sessions.clear()

    return server
