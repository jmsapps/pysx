"""Private authenticated worker control for staged development replacement."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import traceback
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from websockets.asyncio.server import serve

from . import lifecycle, tracing
from .durable import DurableSession
from .durable_host import DurableApplication
from .hosting import request_from_headers
from .lifecycle import capture_local_state, stage_resources
from .ownership import SQLiteOwnershipStore
from .persistence import (
    IncompatibleState,
    MemoryStateStore,
    StateCodec,
    StateIdentity,
    StateSnapshot,
    migrate_state,
)
from .request import RequestContext, browser_id
from .security import AccessDenied
from .session import close_session
from .wire import JSONValue

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from websockets.asyncio.server import ServerConnection

    from .hosting import Application
    from .http import PendingRender, ResumeHandle


_MAX_LOCAL_SLOTS = 4096
_MAX_LOCAL_BYTES = 4 * 1024 * 1024


@dataclass
class Control:
    application: Application
    import_seconds: float = 0
    seeded: dict[str, dict[str, dict[str, object]]] = field(
        default_factory=lambda: dict[str, dict[str, dict[str, object]]]()
    )

    def durable(self) -> DurableApplication[object] | None:
        app = self.application

        if isinstance(app, DurableApplication) and isinstance(app.backend, SQLiteOwnershipStore):
            return cast("DurableApplication[object]", app)

        return None

    def info(self) -> dict[str, JSONValue]:
        app = self.durable()
        result: dict[str, JSONValue] = {"durable": False, "import_seconds": self.import_seconds}

        if app is not None:
            assert isinstance(app.backend, SQLiteOwnershipStore)
            result.update(
                {
                    "durable": True,
                    "application": app.application,
                    "store": str(app.backend.path),
                    "base_path": app.config.base_path,
                }
            )

        if tracing.enabled:
            sources: list[JSONValue] = []

            for name, module in tuple(sys.modules.items()):
                filename = getattr(module, "__file__", None)

                if name == "pysx" or name.startswith("pysx.") or not isinstance(filename, str):
                    continue
                path = Path(filename).resolve()

                if path.suffix == ".py" and any(
                    path.is_relative_to(root) for root in tracing.roots
                ):
                    sources.append(str(path))
            result["sources"] = sources

        return result

    @staticmethod
    def local_identity(browser: str, principal: str | None, location: str) -> str:
        return json.dumps([browser, principal, urlsplit(location).path])

    def freeze_local(self) -> JSONValue:
        rows: list[JSONValue] = []
        dropped = 0
        slots = 0

        for session in tuple(self.application.runtime.graphs):
            if session.closed:
                continue
            scopes: dict[str, JSONValue] = {}

            for path, owner in session.rendered.scopes.owners.items():
                held: dict[str, JSONValue] = {}

                for slot, created in owner.slots.items():
                    value = created.published()

                    try:
                        json.dumps(value)
                    except TypeError, ValueError:
                        dropped += 1

                        continue
                    held[slot] = [cast("JSONValue", value), owner.marks.get(slot, "")]
                    slots += 1

                if held:
                    scopes[path] = cast("JSONValue", held)

            if scopes:
                rows.append(
                    {
                        "identity": self.local_identity(
                            session.browser, session.request.principal, session.location
                        ),
                        "scopes": scopes,
                    }
                )

        if slots > _MAX_LOCAL_SLOTS:
            return {"rows": [], "dropped": dropped, "bounded": "slot limit"}
        size = len(json.dumps(rows).encode())

        if size > _MAX_LOCAL_BYTES:
            return {"rows": [], "dropped": dropped, "bounded": "size limit"}

        return {"rows": rows, "dropped": dropped, "bytes": size}

    def seed_local(self, rows: list[JSONValue]) -> str:
        self.seeded.clear()

        for row in rows:
            if not isinstance(row, dict):
                continue
            identity, scopes = row.get("identity"), row.get("scopes")

            if isinstance(identity, str) and isinstance(scopes, dict):
                self.seeded[identity] = cast("dict[str, dict[str, object]]", scopes)
        lifecycle.restore_source = self.lookup_local

        return "seeded"

    def local_report(self) -> JSONValue:
        entries: list[JSONValue] = []
        preserved = 0

        for session in tuple(self.application.runtime.graphs):
            if session.closed:
                continue

            for owner in session.rendered.scopes.owners.values():
                preserved += len(owner.slots) - len(owner.reset)

                for slot, reason in owner.reset.items():
                    if len(entries) >= 64:
                        break
                    site, _, _ = slot.rpartition("#")
                    entries.append({"slot": site, "reason": reason})

        return {"preserved": preserved, "reset": entries}

    def lookup_local(
        self, browser: str, principal: str | None, location: str
    ) -> dict[str, dict[str, object]] | None:
        return self.seeded.get(self.local_identity(browser, principal, location))

    def inspect(self, headers: dict[str, str]) -> dict[str, JSONValue]:
        app = self.application

        if not tracing.enabled or not app.config.development or app.config.production:
            return {"status": 404}

        try:
            request = app.prepare(
                request_from_headers(
                    app.path("__pysx_dev__/inspect"), list(headers.items()), peer="127.0.0.1"
                )
            )
        except AccessDenied:
            return {"status": 403}
        browser = browser_id(request)
        handles: tuple[PendingRender | ResumeHandle, ...] = (
            *app.runtime.pending.values(),
            *app.runtime.resume_handles.values(),
        )
        allowed = {
            handle.session
            for handle in handles
            if browser and handle.browser == browser and handle.principal == request.principal
        }
        sessions: list[JSONValue] = []

        for graph in allowed:
            if graph.closed or graph.trace is None:
                continue
            assert request.access is not None

            try:
                request.access("inspector", graph.trace.uid)
            except AccessDenied:
                continue
            report = graph.trace.report()
            report["owners"] = len(graph.rendered.scopes.owners)
            report["resources"] = sum(
                len(owner.resources) for owner in graph.rendered.scopes.owners.values()
            )
            report["render_effects"] = len(graph.effects)
            report["handlers"] = len(graph.rendered.handlers)
            report["tasks"] = len(graph.runtime.tasks)
            sessions.append(report)

        return {"status": 200 if sessions else 403, "sessions": sessions}

    async def validate(self, rows: list[JSONValue]) -> str:
        app = self.durable()
        assert app is not None
        runtime = app.runtime
        mode = "hot"
        passthrough = StateCodec[JSONValue](
            lambda value: value, lambda value: value, runtime.codec.max_bytes
        )

        def checked(
            migration: Callable[[JSONValue], JSONValue],
        ) -> Callable[[JSONValue], JSONValue]:
            def apply(value: JSONValue) -> JSONValue:
                try:
                    return passthrough.loads(passthrough.dumps(migration(value)))
                except Exception as error:
                    raise RuntimeError("state migration failed") from error

            return apply

        migrations = {version: checked(migrate) for version, migrate in runtime.migrations.items()}

        for row in rows or [None]:
            store = MemoryStateStore()
            request: RequestContext | None = None
            location = "/"
            identity = StateIdentity(app.application, "probe", "probe", "probe")

            if isinstance(row, dict):
                payload, schema = row.get("payload"), row.get("schema")
                fields, path = row.get("identity"), row.get("path")
                headers, location_value = row.get("headers"), row.get("location")

                if (
                    not isinstance(payload, str)
                    or type(schema) is not int
                    or not isinstance(fields, list)
                    or len(fields) != 4
                    or not all(isinstance(value, str) for value in fields)
                    or not isinstance(path, str)
                    or not isinstance(headers, dict)
                    or not isinstance(location_value, str)
                ):
                    raise ValueError("invalid development snapshot")
                identity = StateIdentity(*cast("list[str]", fields))
                header_pairs = [
                    (key, value) for key, value in headers.items() if isinstance(value, str)
                ]

                try:
                    request = app.prepare(
                        request_from_headers(path, header_pairs, peer="127.0.0.1")
                    )
                except AccessDenied:
                    mode = "fresh"

                    continue

                if request.principal != row.get("principal"):
                    mode = "fresh"

                    continue
                location = location_value
                snapshot = app.backend.load(identity) or StateSnapshot(
                    identity, schema, payload.encode("ascii")
                )

                try:
                    migrated = migrate_state(snapshot, runtime.schema, runtime.codec, migrations)
                except IncompatibleState, ValueError:
                    mode = "fresh"
                else:
                    store.claim(
                        replace(
                            snapshot, schema=runtime.schema, payload=runtime.codec.dumps(migrated)
                        )
                    )
            with stage_resources():
                graph: DurableSession[object] | None = None

                try:
                    graph = DurableSession(
                        runtime.factory,
                        initial=runtime.initial,
                        codec=runtime.codec,
                        store=store,
                        identity=identity,
                        schema=runtime.schema,
                        migrations=runtime.migrations,
                        request=request,
                        location=location,
                        development=True,
                    )
                finally:
                    if graph is not None:
                        await close_session(graph, None, None)

        return mode

    async def commands(self, connection: ServerConnection) -> None:
        current = asyncio.current_task()
        assert current is not None

        async def disconnected() -> None:
            await connection.wait_closed()
            current.cancel()

        monitor = asyncio.create_task(disconnected())
        app = self.durable()
        runtime = app.runtime if app is not None else None
        draining = runtime.draining if runtime is not None else False
        owns_drain = False

        try:
            async with AsyncExitStack() as frozen:
                async for raw in connection:
                    message = cast("dict[str, JSONValue]", json.loads(raw))
                    op = message.get("op")

                    try:
                        result: JSONValue

                        if op == "local_report":
                            result = self.local_report()
                        elif op == "freeze_local":
                            result = self.freeze_local()
                        elif op == "seed_local":
                            seeds = message.get("rows")
                            result = self.seed_local(seeds if isinstance(seeds, list) else [])
                        elif op == "probe":
                            if app is not None and not app.config.security.require_principal:
                                await self.validate([])
                            result = self.info()
                        elif op == "freeze" and app is not None and runtime is not None:
                            owns_drain = True
                            runtime.draining = True
                            graphs = tuple(runtime.graphs)
                            rows: list[JSONValue] = []
                            size = 0

                            for value in graphs:
                                graph = cast("DurableSession[object]", value)

                                if graph.closed:
                                    continue
                                await frozen.enter_async_context(graph.runtime.turn())
                                graph.checkpoint()
                                snapshot = graph.snapshot
                                identity = snapshot.identity
                                rows.append(
                                    {
                                        "identity": [
                                            identity.application,
                                            identity.session,
                                            identity.principal,
                                            identity.browser,
                                        ],
                                        "schema": snapshot.schema,
                                        "payload": snapshot.payload.decode("ascii"),
                                        "path": graph.request.path,
                                        "headers": dict(graph.request.headers),
                                        "principal": graph.request.principal,
                                        "location": graph.routes.location.url,
                                    }
                                )
                                size += len(json.dumps(rows[-1]).encode())

                                if size > 16 * 1024 * 1024 - 1024:
                                    raise RuntimeError("development snapshots exceed 16 MiB")
                            result = rows
                        elif op == "validate" and isinstance(message.get("rows"), list):
                            result = await self.validate(cast("list[JSONValue]", message["rows"]))
                        elif op == "inspect" and isinstance(message.get("headers"), dict):
                            headers = cast("dict[str, JSONValue]", message["headers"])
                            result = self.inspect(
                                {
                                    key: value
                                    for key, value in headers.items()
                                    if isinstance(value, str)
                                }
                            )
                        else:
                            raise ValueError("unsupported development command")
                        await connection.send(json.dumps({"result": result}))
                    except Exception as error:
                        detail = f"{type(error).__name__}: {error}\n"
                        detail += traceback.format_exc(limit=8)
                        await connection.send(json.dumps({"error": detail[-16000:]}))
        except asyncio.CancelledError:
            pass
        finally:
            if runtime is not None and owns_drain:
                runtime.draining = draining
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor


@asynccontextmanager
async def control_server(
    application: Application, port: int, token: str, *, import_seconds: float = 0
) -> AsyncGenerator[None]:
    if application.config.production or not application.config.development or len(token) < 32:
        raise ValueError("worker control requires explicit development mode and a private token")
    source_roots = tuple(Path(path) for path in json.loads(os.environ.get("PYSX_DEV_ROOTS", "[]")))
    tracing.configure(enable=os.environ.get("PYSX_DEV_INSPECT") == "1", source_roots=source_roots)
    capture_local_state(enable=True)
    control = Control(application, import_seconds)
    durable = control.durable()

    if durable is not None:
        # Explicit development fallback; production recovery policy is untouched.
        durable.fresh_on_incompatible = True

    async def handler(connection: ServerConnection) -> None:
        if connection.request is None or not secrets.compare_digest(
            connection.request.headers.get("Authorization", ""), "Bearer " + token
        ):
            await connection.close(1008, "access denied")

            return
        await control.commands(connection)

    async with serve(
        handler, "127.0.0.1", port, max_size=16 * 1024 * 1024, open_timeout=2, close_timeout=1
    ) as server:
        try:
            yield
        finally:
            server.close()
            server.server.abort_clients()
            tracing.configure(enable=False)
            capture_local_state(enable=False)
