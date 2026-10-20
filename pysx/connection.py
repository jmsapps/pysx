"""Host-independent JSON protocol pump and ordered session outbox."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import parse_qs, urlsplit

from .forms import PayloadError, form_edits
from .http import HTTPRuntime, reconcile
from .ownership import OwnerMoved
from .persistence import RecoveryUnavailable
from .request import browser_id
from .routing import Location
from .security import AccessDenied
from .session import close_session
from .transport import TransportError, TransportLimits
from .wire import parse_message

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from .request import RequestContext
    from .session import Session
    from .wire import InitMessage, JSONValue, PatchMessage


class Connection(Protocol):
    request: RequestContext

    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


class BoundedConnection:
    def __init__(self, connection: Connection, limits: TransportLimits) -> None:
        self.raw = connection
        self.request = connection.request
        self.limits = limits
        self.closing = False
        self.failed = False
        self.close_code: int | None = None
        self.reserved = bool(getattr(connection, "reserved", False))

        if self.reserved:
            cast("BoundedConnection", connection).reserved = False

    def abort(self) -> None:
        abort = getattr(self.raw, "abort", None)

        if callable(abort):
            abort()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closing:
            return
        self.closing = True
        self.close_code = code

        try:
            async with asyncio.timeout(self.limits.close_timeout):
                await self.raw.close(code=code, reason=reason)
        except TimeoutError:
            self.abort()
        except asyncio.CancelledError:
            self.abort()

            raise
        except Exception:
            self.abort()

            raise

    async def send(self, message: str) -> None:
        from websockets.exceptions import ConnectionClosedOK

        if self.closing:
            raise TransportError("connection closing")

        if len(message.encode()) > self.limits.max_batch_bytes:
            await self.close(1009, "outbound batch limit")

            raise TransportError("outbound batch limit")
        task = asyncio.create_task(self.raw.send(message), eager_start=True)

        try:
            if task.done():
                task.result()

                return
            done, _pending = await asyncio.wait({task}, timeout=self.limits.send_timeout)

            if not done:
                await self.close(1013, "send deadline")

                raise TransportError("send deadline")
            await task
        except BaseException as error:
            # A healthy peer close can race a push, or stop the reader while
            # this send is pending. Cleanup cancels that send; neither case
            # makes the current-state snapshot unsafe to resume. Cancellation
            # of the connection pump itself still expires the graph below.
            if not isinstance(error, (asyncio.CancelledError, ConnectionClosedOK)):
                self.failed = True

            if not task.done():
                self.abort()

            raise
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            elif not task.cancelled():
                task.exception()

    async def recv(self) -> str | bytes:
        raw = await self.raw.recv()
        await self.check_size(raw)

        return raw

    async def check_size(self, raw: str | bytes) -> None:
        if len(raw.encode() if isinstance(raw, str) else raw) > self.limits.max_frame_bytes:
            await self.close(1009, "inbound frame limit")

            raise TransportError("inbound frame limit")

    async def __aiter__(self) -> AsyncIterator[str | bytes]:
        async for raw in self.raw:
            await self.check_size(raw)
            yield raw


async def serve_connection(runtime: HTTPRuntime, connection: Connection) -> None:
    bounded = bound_connection(connection, runtime.limits)

    try:
        await _serve_connection(runtime, bounded)
    except OwnerMoved as error:
        await bounded.close(4010, error.worker)
    except RecoveryUnavailable:
        await bounded.close(1013, "durable state unavailable")
    finally:
        if bounded.reserved:
            runtime.unreserve()
            bounded.reserved = False


def bound_connection(connection: Connection, limits: TransportLimits) -> BoundedConnection:
    return (
        connection
        if isinstance(connection, BoundedConnection)
        else BoundedConnection(connection, limits)
    )


async def _serve_connection(runtime: HTTPRuntime, connection: BoundedConnection) -> None:
    request = connection.request

    def authorize(action: str, target: str = "") -> None:
        if request.access is not None:
            request.access(action, target)

    protocol = parse_qs(urlsplit(request.path).query).get("protocol", ["2"])
    modern = protocol == ["3"]

    if runtime.owner_hint is not None and not modern:
        await connection.close(1008, "durable sessions require protocol 3")

        return

    if protocol not in (["2"], ["3"]):
        await connection.close(code=1008, reason="unsupported protocol")

        return
    supplied = parse_qs(urlsplit(request.path).query).get("location", [request.base_path or "/"])

    try:
        Location.parse(supplied[0])
    except ValueError:
        await connection.close(code=1008, reason="invalid initial location")

        return
    adoption = parse_qs(urlsplit(request.path).query, keep_blank_values=True).get("adoption", [])
    adopted = bool(adoption)
    resuming = modern and parse_qs(urlsplit(request.path).query).get("resume") == ["1"]
    recovering = modern and parse_qs(urlsplit(request.path).query).get("recover") == ["1"]
    rebuilt = False
    volatile_rebuild = False
    local_handoff = False
    hello: dict[str, object] | None = None

    def reconstruct(message: dict[str, object]) -> Session:
        authorize("resume")
        token = message.get("recovery")

        if not isinstance(token, str):
            raise ValueError("recovery credential required")

        return runtime.recover(token, request, location=supplied[0])

    if resuming or recovering:
        try:
            authorize("resume")
            message = parse_message(
                await asyncio.wait_for(connection.recv(), runtime.limits.hello_timeout),
                limit=runtime.limits.max_frame_bytes,
            )

            if message["t"] != "hello" or message.get("edits"):
                raise PayloadError("expected resume hello")
            hello = message

            try:
                if not resuming:
                    raise ValueError("explicit reconstruction")
                session = runtime.resume(
                    cast("str", message["resume"]),
                    request,
                    cast("str", message["epoch"]),
                    cast("int", message["state_rev"]),
                    location=supplied[0],
                )

                if session.transport.epoch != message["epoch"]:
                    rebuilt = True
                    volatile_rebuild = True
                    local_handoff = True
            except ValueError, KeyError:
                try:
                    session = reconstruct(message)
                except ValueError:
                    if not runtime.development:
                        raise
                    authorize("websocket", "/ws")

                    if not connection.reserved and not runtime.reserve():
                        raise
                    connection.reserved = True

                    try:
                        session = runtime.create_reserved(request, location=supplied[0])
                    finally:
                        runtime.unreserve()
                        connection.reserved = False
                    volatile_rebuild = True
                rebuilt = True
        except PayloadError, ValueError, KeyError, TimeoutError, AccessDenied:
            await connection.close(4004, "resume unavailable")

            return
    elif adopted:
        try:
            authorize("adoption")
            session = runtime.claim(adoption[0], request, browser_id(request))
        except AccessDenied:
            await connection.close(code=1008, reason="invalid adoption")

            return
        except ValueError:
            if not modern or runtime.owner_hint is None:
                await connection.close(1008, "invalid adoption")

                return

            try:
                hello = parse_message(
                    await asyncio.wait_for(connection.recv(), runtime.limits.hello_timeout),
                    limit=runtime.limits.max_frame_bytes,
                )

                if hello["t"] != "hello":
                    raise PayloadError("expected recovery hello")
                session = reconstruct(hello)
                rebuilt = True
                adopted = False
            except ValueError, PayloadError, AccessDenied, TimeoutError:
                await connection.close(4004, "adoption recovery unavailable")

                return

        if not rebuilt:
            try:
                message = parse_message(
                    await asyncio.wait_for(connection.recv(), runtime.limits.hello_timeout),
                    limit=runtime.limits.max_frame_bytes,
                )

                if message.get("t") != ("hello" if modern else "adopt"):
                    raise PayloadError("expected adoption edits")
                async with session.runtime.turn():
                    authorize("adoption")
                    edits = form_edits(message.get("edits"))

                    for handler, _value, _revision in edits:
                        authorize("event", handler)
                    reconcile(session, edits)
                    session.routes.observe(supplied[0])
            except RecoveryUnavailable:
                runtime.release(session)

                raise
            except Exception:
                runtime.release(session)
                await connection.close(code=1008, reason="invalid adoption edits")

                return
            except asyncio.CancelledError:
                runtime.release(session)

                raise
    else:
        if not connection.reserved:
            if not runtime.reserve():
                await connection.close(1013, "graph capacity")

                return
            connection.reserved = True

        try:
            if modern:
                try:
                    message = parse_message(
                        await asyncio.wait_for(connection.recv(), runtime.limits.hello_timeout),
                        limit=runtime.limits.max_frame_bytes,
                    )

                    if message["t"] != "hello":
                        raise PayloadError("expected hello")
                except Exception:
                    await connection.close(code=1008, reason="invalid hello")

                    return

            try:
                authorize("websocket", "/ws")
            except AccessDenied:
                await connection.close(1008, "access denied")

                return
            session = runtime.create_reserved(request, location=supplied[0])
        finally:
            runtime.unreserve()
            connection.reserved = False
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=runtime.limits.event_queue)
    announced_mounts: set[str] = set()
    send_lock = asyncio.Lock()
    resume_token = runtime.attach(session, request) if modern else None

    async def send(message: dict[str, object]) -> None:
        async with send_lock:
            if session.closed:
                if session.recovery_failed:
                    await connection.close(1013, "durable state unavailable")

                    raise RecoveryUnavailable("durable state unavailable")

                if session.revoked:
                    await connection.close(1008, "access denied")

                    raise AccessDenied("access denied")
                await connection.close(1001, "session closed")

                raise TransportError("session closed")

            try:
                authorize("command", str(message.get("t", "")))
            except AccessDenied:
                await connection.close(1008, "access denied")

                raise

            if session.pressured:
                await connection.close(1013, "session outbox limit")

                raise TransportError("session outbox limit")

            if modern:
                message = session.transport.envelope(message)
            session.checkpoint()
            await connection.send(json.dumps(message))

            if session.trace is not None and message.get("t") in ("patch", "result", "repair"):
                operations = message.get("ops")
                session.trace.record(
                    "patch.batch",
                    count=len(cast("list[object]", operations))
                    if isinstance(operations, list)
                    else 0,
                )

    async def send_repair() -> None:
        announced_mounts.clear()
        await send(session.transport.repair(session.rendered.css))

    async def send_mounts() -> None:
        pending = session.rendered.scopes.pending_mounts()
        announced_mounts.intersection_update(pending)
        tokens = [token for token in pending if token not in announced_mounts]

        for start in range(0, len(tokens), 1024):
            await send({"t": "mount", "ids": tokens[start : start + 1024]})
            announced_mounts.update(tokens[start : start + 1024])

    async def send_navigation() -> None:
        commands, session.routes.commands = session.routes.commands, []

        for command in commands:
            await send(dict(command))

    async def send_patch() -> None:
        if session.pending:
            ops, session.pending = session.pending, []
            await send({"t": "patch", "ops": ops})

    async def send_command(message: dict[str, JSONValue]) -> None:
        await send_patch()
        await send_navigation()
        await send(dict(message))

    session.rendered.dom.sender = send_command

    async def handle_message(message: dict[str, object]) -> bool:
        try:
            authorize("event", str(message.get("h", message.get("t", ""))))

            if message.get("after") is not None:
                authorize("event", str(message["after"]))

            for handler, _value, _revision in form_edits(message.get("edits")):
                authorize("event", handler)
        except AccessDenied:
            await connection.close(1008, "access denied")

            return False
        except PayloadError:
            pass  # Shape validation below produces the protocol's invalid result.

        if modern:
            admission = session.transport.accept(message)

            if admission != "accepted":
                await send(
                    {
                        "t": "ack",
                        "seq": message["seq"],
                        "status": admission,
                        "expected": session.transport.sequence + 1,
                    }
                )

                if admission != "duplicate":
                    await send_repair()
                    await send_mounts()

                return True
        handler_id = message.get("h", "")
        revision = message.get("rev")
        after = message.get("after")

        if (
            not isinstance(handler_id, str)
            or (revision is not None and (type(revision) is not int or revision < 0))
            or (after is not None and not isinstance(after, str))
        ):
            await send({"t": "result", "h": "", "status": "invalid", "ops": []})

            return True

        try:
            if message["t"] == "event":
                edits = form_edits(message.get("edits"))

                if any(hid not in session.rendered.bindings for hid, _value, _rev in edits):
                    raise PayloadError("unknown binding")

                if after is not None and after not in session.rendered.handlers:
                    if handler_id not in session.rendered.bindings:
                        raise PayloadError("unknown chained handler")
                    after = None

            if message.get("t") == "location":
                url = message.get("url")

                if not isinstance(url, str) or not session.routes.accept_revision(
                    message.get("rev")
                ):
                    raise PayloadError("stale location revision")

                try:
                    session.routes.observe(url)
                except ValueError as error:
                    raise PayloadError("invalid location observation") from error
                session.routes.commands.append(
                    {
                        "t": "navigation",
                        "url": session.routes.location.url,
                        "rev": session.routes.revision,
                        "mode": "observe",
                    }
                )
                ops, session.pending = session.pending, []
            elif message.get("t") == "mounted":
                tokens = message.get("ids")

                if isinstance(tokens, list):
                    supplied = cast("list[object]", tokens)

                    if len(supplied) <= 1024 and all(isinstance(token, str) for token in supplied):
                        session.rendered.scopes.acknowledge(cast("list[str]", supplied))
                ops, session.pending = session.pending, []
            else:
                nav_revision = message.get("nav_rev")

                if nav_revision is not None and not session.routes.accept_revision(nav_revision):
                    raise PayloadError("stale navigation revision")
                outcome = await session.dispatch_result_async(
                    handler_id,
                    message.get("v"),
                    revision,
                    after=after,
                    edits=form_edits(message.get("edits")),
                    event=message.get("event"),
                    navigation=message.get("navigation") is True,
                )

                if outcome["status"] != "success":
                    if modern:
                        sequence = cast("int", message["seq"])
                        session.transport.acknowledge(sequence, outcome["status"])
                        await send({**outcome, "seq": sequence})
                    else:
                        await send(dict(outcome))
                    await send_navigation()
                    await send_mounts()

                    if session.rendered.unrepaired:
                        await connection.close(
                            code=1011, reason="render recovery requires a boundary"
                        )

                        return False

                    return True
                ops = outcome["ops"]
        except RecoveryUnavailable:
            raise
        except PayloadError:
            repair, session.pending = session.pending, []
            result: dict[str, object] = {
                "t": "result",
                "h": handler_id,
                "status": "invalid",
                "ops": repair,
            }

            if modern:
                sequence = cast("int", message["seq"])
                session.transport.acknowledge(sequence, "invalid")
                result["seq"] = sequence
            await send(result)
            await send_navigation()
            await send_mounts()

            return True
        except Exception:
            await connection.close(code=1011, reason="event handler failed")

            return False

        if session.rendered.failures:
            info = session.rendered.failures[0]
            session.rendered.failures.clear()
            await send(
                {
                    "t": "result",
                    "h": handler_id,
                    "status": "error",
                    "ops": ops,
                    "error": info.wire(),
                }
            )
        elif ops:
            patch: PatchMessage = {"t": "patch", "ops": ops}
            await send(dict(patch))

        if modern:
            sequence = cast("int", message["seq"])
            session.transport.acknowledge(sequence, "success")
            await send({"t": "ack", "seq": sequence, "status": "success", "expected": sequence + 1})
        await send_navigation()
        await send_mounts()

        return True

    async def event_worker() -> None:
        while True:
            message = await queue.get()
            async with session.runtime.turn():
                if not await handle_message(message):
                    return

    worker: asyncio.Task[None] | None = None
    outbox: asyncio.Task[None] | None = None
    reader: asyncio.Task[None] | None = None

    async def push_worker() -> None:
        while True:
            await session.changed.wait()
            async with session.runtime.lock:
                session.changed.clear()

                if session.closed:
                    if session.recovery_failed:
                        await connection.close(1013, "durable state unavailable")
                    elif session.revoked:
                        await connection.close(1008, "access denied")
                    else:
                        await connection.close(1001, "session closed")

                    return

                if session.pressured:
                    await connection.close(1013, "session outbox limit")

                    raise TransportError("session outbox limit")

                while session.rendered.failures:
                    info = session.rendered.failures.pop(0)
                    ops, session.pending = session.pending, []
                    await send(
                        {
                            "t": "result",
                            "h": "",
                            "status": "error",
                            "ops": ops,
                            "error": info.wire(),
                        }
                    )

                    if session.rendered.unrepaired:
                        await connection.close(
                            code=1011, reason="render recovery requires a boundary"
                        )

                        return
                await send_patch()
                await send_navigation()
                await send_mounts()

    async def read_worker() -> None:
        async for raw in connection:
            try:
                message = parse_message(raw, modern=modern, limit=runtime.limits.max_frame_bytes)
            except PayloadError:
                await send({"t": "result", "h": "", "status": "invalid", "ops": []})

                continue

            if message.get("t") == "dom_reply":
                try:
                    authorize("dom_reply", str(message.get("id", "")))
                except AccessDenied:
                    await connection.close(1008, "access denied")

                    return

                if not modern or message["epoch"] == session.transport.epoch:
                    session.rendered.dom.reply({**message, "version": 1})
            elif message.get("t") == "repair" and modern:
                try:
                    authorize("repair")
                except AccessDenied:
                    await connection.close(1008, "access denied")

                    return
                async with session.runtime.lock:
                    await send_repair()
                    await send_mounts()
            elif message.get("t") in ("event", "mounted", "location"):
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    await connection.close(code=1008, reason="event queue limit")

                    return

    failure: BaseException | None = None

    try:
        initial: InitMessage = {
            "t": "init",
            "html": session.rendered.body,
            "css": session.rendered.css,
            "routing": bool(session.routes.routers),
        }
        attachment: dict[str, object] = (
            {
                **session.transport.repair(session.rendered.css),
                "resume": resume_token,
                "routing": bool(session.routes.routers),
                "nav_rev": session.routes.revision,
            }
            if resuming and not rebuilt
            else {"t": "adopted", "routing": bool(session.routes.routers), "resume": resume_token}
            if adopted
            else {**dict(initial), **({"resume": resume_token} if modern else {})}
        )

        if rebuilt:
            attachment["html"] = session.transport.tree.markup()
            session.pending.clear()
            attachment[
                "recovered"
                if local_handoff
                or (
                    not volatile_rebuild
                    and hello is not None
                    and runtime.recovery_token(session) == hello.get("recovery")
                )
                else "fresh"
            ] = True

        if runtime.development:
            attachment["development"] = True
        recovery = runtime.recovery_token(session)

        if modern and recovery is not None:
            attachment["recovery"] = recovery
            attachment["owner"] = runtime.owner_hint
        await send(attachment)
        await send_patch()

        if resuming and session.routes.routers:
            await send(
                {
                    "t": "navigation",
                    "url": session.routes.location.url,
                    "rev": session.routes.revision,
                    "mode": "replace",
                }
            )
        await send_navigation()
        await send_mounts()
        worker = asyncio.create_task(event_worker())
        outbox = asyncio.create_task(push_worker())
        reader = asyncio.create_task(read_worker())
        done, _pending = await asyncio.wait(
            (worker, outbox, reader), return_when=asyncio.FIRST_COMPLETED
        )

        for task in done:
            await task
    except BaseException as error:
        failure = error

        raise
    finally:
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

        if outbox is not None:
            outbox.cancel()
            await asyncio.gather(outbox, return_exceptions=True)

        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            worker = None
        retain = (
            modern
            and not runtime.closed
            and not session.closed
            and not connection.closing
            and not connection.failed
            and not session.pressured
            and not session.rendered.unrepaired
            and (
                failure is None
                or isinstance(failure, OSError)
                or type(failure).__name__.startswith("ConnectionClosed")
            )
            and not isinstance(failure, TransportError)
            and getattr(getattr(failure, "rcvd", None), "code", 1000) in (1000, 1001, 1006)
            and getattr(connection.raw, "close_code", 1000) in (None, 1000, 1001, 1006)
        )

        if retain:
            runtime.detach(session)
        else:
            owned = set(session.runtime.tasks)

            if session.runtime.worker is not None:
                owned.add(session.runtime.worker)

            try:
                await close_session(session, worker, failure)
            finally:
                runtime.release(session)

            for owned_task in owned:
                owned_task.cancel()

            if owned:
                with suppress(TimeoutError):
                    async with asyncio.timeout(runtime.limits.close_timeout):
                        await asyncio.gather(*owned, return_exceptions=True)
