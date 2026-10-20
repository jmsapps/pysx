"""Private authenticated worker control for staged development replacement."""

from __future__ import annotations

import asyncio
import json
import math
import os
import secrets
import sys
import time
import traceback
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from websockets.asyncio.server import serve

from . import tracing
from .durable import DurableSession
from .durable_host import DurableApplication
from .hosting import request_from_headers
from .lifecycle import capture_local_state, local_value_kind, restoring_local_state, stage_resources
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
from .session import Session, close_session
from .wire import JSONValue

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from websockets.asyncio.server import ServerConnection

    from .hosting import Application
    from .http import PendingRender, ResumeHandle


_MAX_LOCAL_SLOTS = 4096
_MAX_LOCAL_BYTES = 4 * 1024 * 1024
_MAX_LOCAL_SESSIONS = 256


@dataclass
class Control:
    application: Application
    import_seconds: float = 0
    seeded: dict[str, dict[str, JSONValue]] = field(
        default_factory=lambda: dict[str, dict[str, JSONValue]]()
    )
    local_resets: list[JSONValue] = field(default_factory=list[JSONValue])

    def durable(self) -> DurableApplication[object] | None:
        app = self.application

        if isinstance(app, DurableApplication) and isinstance(app.backend, SQLiteOwnershipStore):
            return cast("DurableApplication[object]", app)

        return None

    def info(self) -> dict[str, JSONValue]:
        app = self.durable()
        result: dict[str, JSONValue] = {
            "durable": False,
            "local": not isinstance(self.application, DurableApplication),
            "base_path": self.application.config.base_path,
            "import_seconds": self.import_seconds,
        }

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

    def freeze_local(self) -> JSONValue:
        now = time.monotonic()
        retained = {
            token: row for token, row in self.seeded.items() if cast("float", row["expires"]) > now
        }
        dropped = 0

        for token, handle in tuple(self.application.runtime.resume_handles.items()):
            session = handle.session

            if session.closed or not handle.browser:
                continue
            scopes: dict[str, JSONValue] = {}

            for path, owner in session.rendered.scopes.owners.items():
                held: dict[str, JSONValue] = {}

                for slot, created in owner.slots.items():
                    value = created.published()
                    metadata: dict[str, JSONValue] = {"kind": owner.kinds.get(slot, "unsupported")}

                    try:
                        kind = local_value_kind(value)
                        restored: object = json.loads(json.dumps(value, allow_nan=False))

                        if kind != local_value_kind(restored) or value != restored:
                            raise ValueError("value did not round-trip")
                    except (TypeError, ValueError, OverflowError, RecursionError) as error:
                        dropped += 1
                        metadata["reason"] = (
                            str(error)
                            if isinstance(error, ValueError)
                            else "unsupported value type"
                        )
                        value = None
                    held[slot] = [cast("JSONValue", value), owner.marks.get(slot, ""), metadata]

                if held:
                    scopes[path] = cast("JSONValue", held)

            retained[token] = {
                "resume": token,
                "browser": handle.browser,
                "principal": handle.principal,
                "epoch": session.transport.epoch,
                "revision": session.transport.revision,
                "location": session.routes.location.url,
                "path": session.request.path,
                "headers": dict(session.request.headers),
                "scopes": scopes,
                "expires": now + 30,
            }

        rows = cast("list[JSONValue]", list(retained.values()))
        slots = sum(
            len(cast("dict[str, JSONValue]", scope))
            for row in retained.values()
            for scope in cast("dict[str, JSONValue]", row["scopes"]).values()
        )

        if len(rows) > _MAX_LOCAL_SESSIONS:
            return {"rows": [], "dropped": slots, "bounded": "session limit"}

        if slots > _MAX_LOCAL_SLOTS:
            return {"rows": [], "dropped": slots, "bounded": "slot limit"}
        size = len(json.dumps(rows).encode())

        if size > _MAX_LOCAL_BYTES:
            return {"rows": [], "dropped": slots, "bounded": "size limit"}

        return {"rows": rows, "dropped": dropped, "bytes": size}

    @staticmethod
    def checked_local(rows: list[JSONValue]) -> dict[str, dict[str, JSONValue]]:
        if len(rows) > _MAX_LOCAL_SESSIONS or len(json.dumps(rows).encode()) > _MAX_LOCAL_BYTES:
            raise ValueError("development handoffs exceed their bounds")
        checked: dict[str, dict[str, JSONValue]] = {}
        slots = 0

        for row in rows:
            if not isinstance(row, dict) or not all(
                isinstance(row.get(name), str) and row[name]
                for name in ("resume", "browser", "epoch", "location", "path")
            ):
                raise ValueError("invalid development handoff identity")
            scopes, headers = row.get("scopes"), row.get("headers")
            expires, revision = row.get("expires"), row.get("revision")

            if (
                not isinstance(scopes, dict)
                or not all(isinstance(scope, dict) for scope in scopes.values())
                or not isinstance(headers, dict)
                or not all(isinstance(value, str) for value in headers.values())
                or (row.get("principal") is not None and not isinstance(row["principal"], str))
                or type(revision) is not int
                or revision < 0
                or type(expires) not in (int, float)
                or not math.isfinite(cast("float", expires))
            ):
                raise ValueError("invalid development handoff snapshot")
            slots += sum(len(cast("dict[str, JSONValue]", scope)) for scope in scopes.values())

            if slots > _MAX_LOCAL_SLOTS:
                raise ValueError("development handoffs exceed their slot limit")

            if cast("float", expires) > time.monotonic():
                token = cast("str", row["resume"])

                if token in checked:
                    raise ValueError("duplicate development handoff")
                checked[token] = row

        return checked

    def seed_local(self, rows: list[JSONValue]) -> str:
        self.seeded = self.checked_local(rows)
        self.application.runtime.development_resume = self.resume_local if self.seeded else None

        return "seeded"

    def resume_local(
        self,
        token: str,
        request: RequestContext,
        epoch: str,
        revision: int,
        location: str,
    ) -> Session | None:
        row = self.seeded.get(token)

        if row is None:
            return None

        if cast("float", row["expires"]) <= time.monotonic():
            self.seeded.pop(token)

            return None

        if (
            row["browser"] != browser_id(request)
            or row["principal"] != request.principal
            or row["epoch"] != epoch
            or revision > cast("int", row["revision"])
            or row["location"] != location
        ):
            raise ValueError("unknown or mismatched development handoff")
        runtime = self.application.runtime

        if not runtime.reserve():
            raise ValueError("development graph capacity")

        try:
            with restoring_local_state(cast("dict[str, dict[str, object]]", row["scopes"])):
                graph = runtime.create_reserved(
                    request, browser=browser_id(request), location=location
                )
        finally:
            runtime.unreserve()
        self.seeded.pop(token)

        return graph

    async def validate_local(self, rows: list[JSONValue]) -> None:
        checked = self.checked_local(rows)
        app = self.application
        self.local_resets.clear()

        probes: list[dict[str, JSONValue] | None] = list(checked.values())

        for row in probes or [None]:
            try:
                request = app.prepare(
                    request_from_headers(
                        cast("str", row["path"]) if row else app.path("/"),
                        list(cast("dict[str, str]", row["headers"]).items()) if row else [],
                        peer="127.0.0.1",
                    )
                )

                if row is not None and request.principal != row["principal"]:
                    continue

                if request.access is not None:
                    request.access("http", request.path)
            except AccessDenied:
                continue

            with (
                stage_resources(),
                restoring_local_state(
                    cast("dict[str, dict[str, object]]", row["scopes"]) if row else None,
                ),
            ):
                graph: Session | None = None

                try:
                    graph = Session(
                        app.runtime.app,
                        request=request,
                        development=True,
                        browser=cast("str", row["browser"]) if row else "",
                        location=cast("str", row["location"]) if row else request.path,
                        mutation_capacity=app.config.mutation_capacity,
                        task_capacity=app.config.task_capacity,
                    )

                    if row is not None:
                        scopes = cast("dict[str, dict[str, object]]", row["scopes"])

                        for path, held in scopes.items():
                            owner = graph.rendered.scopes.owners.get(path)

                            for slot in held:
                                if (owner is None or slot not in owner.slots) and len(
                                    self.local_resets
                                ) < 64:
                                    self.local_resets.append(
                                        {
                                            "slot": slot.rpartition("#")[0],
                                            "reason": "scope removed"
                                            if owner is None
                                            else "slot removed",
                                        }
                                    )
                finally:
                    if graph is not None:
                        await close_session(graph, None, None)

    def local_report(self) -> JSONValue:
        entries: list[JSONValue] = list(self.local_resets)
        preserved = 0

        for session in tuple(self.application.runtime.graphs):
            if session.closed:
                continue

            for owner in session.rendered.scopes.owners.values():
                preserved += len(owner.preserved)

                for slot, reason in owner.reset.items():
                    if len(entries) >= 64:
                        break
                    site, _, _ = slot.rpartition("#")
                    entries.append({"slot": site, "reason": reason})

        for value in entries:
            entry = cast("dict[str, JSONValue]", value)
            slot = str(entry["slot"])
            filename, _, number = slot.rpartition(":")

            if number.isdecimal() and any(
                Path(filename).resolve().is_relative_to(root) for root in tracing.roots
            ):
                source = tracing.source_link(Path(filename), int(number))
                entry["source"] = {
                    "file": source["file"],
                    "line": source["line"],
                    "uri": source["uri"],
                }
                entry["slot"] = f"{source['file']}:{number}"
            else:
                entry["slot"] = slot[:256] if slot.startswith("named:") else Path(slot).name[:256]

        return {"preserved": preserved, "reset": entries, "scope": "workspace"}

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
        app = cast("DurableApplication[object]", self.application)
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
        runtime = self.application.runtime
        draining = runtime.draining
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
                            owns_drain = True
                            runtime.draining = True

                            for graph in tuple(runtime.graphs):
                                if not graph.closed:
                                    await frozen.enter_async_context(graph.runtime.turn())
                            result = self.freeze_local()
                        elif op == "seed_local":
                            seeds = message.get("rows")
                            local_rows = seeds if isinstance(seeds, list) else []
                            await self.validate_local(local_rows)
                            result = self.seed_local(local_rows)
                        elif op == "probe":
                            application = self.application

                            if (
                                message.get("render", True) is not False
                                and not application.config.security.require_principal
                            ):
                                if isinstance(application, DurableApplication):
                                    await self.validate([])
                                else:
                                    await self.validate_local([])
                            result = self.info()
                        elif op == "freeze" and app is not None:
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
            if owns_drain:
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
