"""Authenticated reconstruction and bounded worker ownership for hosted apps."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from .durable import DurableSession
from .hosting import Application
from .http import HTTPResult, HTTPRuntime
from .ownership import LeasedStateStore, OwnerMoved, OwnershipStore
from .persistence import (
    IncompatibleState,
    RecoveryUnavailable,
    StateCodec,
    StateIdentity,
    StateSnapshot,
    StoreUnavailable,
)
from .request import browser_id
from .security import AccessDenied

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from string.templatelib import Template

    from .hosting import HostConfig
    from .reactive import Signal
    from .render import Fragment
    from .request import RequestContext
    from .session import Session
    from .wire import JSONValue


def _principal_key(principal: str | None) -> str:
    value = (
        b"anonymous"
        if principal is None
        else b"principal:" + principal.encode("utf-8", "surrogatepass")
    )

    return hashlib.sha256(value).hexdigest()


class _DurableRuntime[T](HTTPRuntime):
    def __init__(
        self,
        host: DurableApplication[T],
        app: Callable[[Signal[T]], Template | Fragment],
        initial: Callable[[], T],
        codec: StateCodec[T],
        schema: int,
        migrations: Mapping[int, Callable[[JSONValue], JSONValue]],
    ) -> None:
        super().__init__(
            host.app,
            capacity=host.config.render_capacity,
            ttl=host.config.render_ttl,
            development=host.config.development,
            mutation_capacity=host.config.mutation_capacity,
            task_capacity=host.config.task_capacity,
            limits=host.config.transport,
        )
        self.host = host
        self.factory = app
        self.initial = initial
        self.codec = codec
        self.schema = schema
        self.migrations = dict(migrations)
        self.owner_hint = host.worker
        self.draining = False

    def _identity(self, token: str, request: RequestContext, browser: str) -> StateIdentity:
        if not browser or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
            raise ValueError("invalid recovery identity")
        principal = _principal_key(request.principal)

        return StateIdentity(self.host.application, token, principal, browser)

    def reserve(self) -> bool:
        return not self.draining and super().reserve()

    def _open(self, identity: StateIdentity, request: RequestContext, location: str) -> Session:
        graph = DurableSession(
            self.factory,
            initial=self.initial,
            codec=self.codec,
            store=self.host.state_store,
            identity=identity,
            schema=self.schema,
            migrations=self.migrations,
            request=request,
            location=location,
            development=self.development,
            mutation_capacity=self.mutation_capacity,
            task_capacity=self.task_capacity,
            outbox_bytes=self.limits.outbox_bytes,
        )
        self.graphs.add(graph)

        return graph

    def create_reserved(
        self, request: RequestContext, *, location: str | None = None, browser: str | None = None
    ) -> Session:
        if self.draining:
            raise RecoveryUnavailable("worker is draining")
        identity = self._identity(
            secrets.token_urlsafe(32), request, browser or browser_id(request)
        )

        return self._open(identity, request, location or request.path)

    def recovery_token(self, session: Session) -> str | None:
        if isinstance(session, DurableSession):
            return cast("DurableSession[T]", session).snapshot.identity.session

        return None

    def recover(self, token: str, request: RequestContext, *, location: str) -> Session:
        identity = self._identity(token, request, browser_id(request))

        if self.host.state_store.load(identity) is None:
            raise ValueError("unknown recovery identity")

        for handle, pending in tuple(self.pending.items()):
            graph = pending.session

            if self.recovery_token(graph) == token:
                assert isinstance(graph, DurableSession)
                typed = cast("DurableSession[T]", graph)

                if typed.snapshot.identity != identity:
                    raise ValueError("mismatched recovery identity")
                self.host.state_store.check(typed.snapshot)
                self.pending.pop(handle)
                pending.expiry.cancel()

                return typed

        for graph in tuple(self.graphs):
            if self.recovery_token(graph) == token:
                self.release(graph)
        lease = self.host.backend.route(identity)

        if lease is not None and lease.worker != self.host.worker:
            raise OwnerMoved(lease.worker)

        if self.draining or not self.reserve():
            raise RecoveryUnavailable("recovery graph capacity")

        try:
            try:
                return self._open(identity, request, location)
            except IncompatibleState:
                if not self.host.fresh_on_incompatible:
                    raise RecoveryUnavailable("incompatible application state") from None
                fresh = self._identity(secrets.token_urlsafe(32), request, browser_id(request))

                return self._open(fresh, request, location)
        except RecoveryUnavailable:
            raise
        except Exception as error:
            raise RecoveryUnavailable("application reconstruction unavailable") from error
        finally:
            self.unreserve()

    def release(self, session: Session) -> None:
        snapshot = cast("DurableSession[T]", session).snapshot

        try:
            super().release(session)
        finally:
            try:
                self.host.backend.release_owner(snapshot, self.host.worker)
            except RecoveryUnavailable:
                self.host.store_available = False


class DurableApplication[T](Application):
    """One fresh declared-state graph per tab, with a single leased writer."""

    def __init__(
        self,
        app: Callable[[Signal[T]], Template | Fragment],
        *,
        initial: Callable[[], T],
        codec: StateCodec[T],
        store: OwnershipStore,
        application: str,
        worker: str | None = None,
        lease_seconds: float = 3,
        schema: int = 1,
        migrations: Mapping[int, Callable[[JSONValue], JSONValue]] | None = None,
        fresh_on_incompatible: bool = False,
        config: HostConfig | None = None,
    ) -> None:
        StateSnapshot(StateIdentity(application, "_", "_", "_"), schema, b"null")

        if type(fresh_on_incompatible) is not bool:
            raise ValueError("fresh_on_incompatible must be an explicit boolean")
        self.worker = secrets.token_urlsafe(18) if worker is None else worker
        self.application = application
        self.backend = store
        self.state_store = LeasedStateStore(store, self.worker, lease_seconds)
        self.fresh_on_incompatible = fresh_on_incompatible
        self.store_available = True
        self.renewing = True
        self.renewal: asyncio.Task[None] | None = None
        self.renewed = 0.0
        self.fenced_total = 0
        self.store_failures_total = 0

        def unavailable() -> Fragment:
            raise RuntimeError("durable apps require the session factory")

        super().__init__(unavailable, config=config)
        self.runtime = _DurableRuntime(self, app, initial, codec, schema, migrations or {})

    async def start(self) -> None:
        await super().start()

        if self.renewal is None:
            self._probe_store()
            self.renewed = asyncio.get_running_loop().time()
            self.renewal = asyncio.create_task(self._renew())

    def _probe_store(self) -> None:
        try:
            self.backend.renew([], self.worker, self.state_store.seconds)
            self.store_available = True
        except RecoveryUnavailable:
            self.store_available = False
            self.store_failures_total += 1

    def operational_status(self) -> dict[str, int]:
        """Bounded numeric observations; no identities, routes or credentials."""
        assert isinstance(self.runtime, _DurableRuntime)
        graphs = tuple(self.runtime.graphs)
        ready = (
            self.started
            and not self.closed
            and not self.runtime.draining
            and self.store_available
            and len(graphs) + self.runtime.reservations < self.runtime.limits.graphs
        )

        return {
            "ready": int(ready),
            "store_available": int(self.store_available),
            "draining": int(self.runtime.draining),
            "graphs": len(graphs),
            "capacity": self.runtime.limits.graphs,
            "pending": len(self.runtime.pending),
            "detached": len(self.runtime.detached),
            "reservations": self.runtime.reservations,
            "connections": len(self.connections),
            "handshakes": len(self.handshakes),
            "fenced_total": self.fenced_total,
            "store_failures_total": self.store_failures_total,
        }

    async def _http(self, request: RequestContext, *, method: str = "GET") -> HTTPResult:
        path = urlsplit(request.path).path
        prefix = request.base_path or self.config.base_path

        if path == prefix + "/_pysx/ready":
            return await self._operational(method=method, metrics=False)

        if path != prefix + "/_pysx/metrics":
            return await super()._http(request, method=method)

        try:
            context = self.prepare(request)
            assert context.access is not None
            context.access("http", path)
        except AccessDenied:
            return HTTPResult(403, (("cache-control", "private, no-store"),), b"access denied")

        return await self._operational(method=method, metrics=True)

    async def _operational(self, *, method: str, metrics: bool) -> HTTPResult:
        headers = (("content-type", "text/plain; charset=utf-8"), ("cache-control", "no-store"))

        if method not in ("GET", "HEAD"):
            return HTTPResult(405, (*headers, ("allow", "GET, HEAD")), b"")

        if not self.closed:
            await self.start()
            self._probe_store()
        values = self.operational_status()

        if metrics:
            status = 200
            body = "".join(
                f"pysx_durable_{key} {value}\n" for key, value in values.items()
            ).encode()
        else:
            status = 200 if values["ready"] else 503
            body = b"ready\n" if values["ready"] else b"unavailable\n"

        return HTTPResult(
            status,
            (*headers, ("content-length", str(len(body)))),
            b"" if method == "HEAD" else body,
        )

    async def _renew(self) -> None:
        while not self.closed:
            await asyncio.sleep(self.state_store.seconds / 3)

            if not self.renewing:
                continue
            graphs = tuple(
                cast("DurableSession[T]", graph)
                for graph in self.runtime.graphs
                if isinstance(graph, DurableSession) and not graph.closed
            )

            try:
                fenced = self.backend.renew(
                    [graph.snapshot for graph in graphs], self.worker, self.state_store.seconds
                )
                self.store_available = True
                self.renewed = asyncio.get_running_loop().time()
            except StoreUnavailable:
                self.store_available = False
                self.store_failures_total += 1
                fenced = self._lapsed(graphs)
            except RecoveryUnavailable:
                self.store_available = False
                self.store_failures_total += 1
                fenced = tuple(graph.snapshot.identity for graph in graphs)

            for graph in graphs:
                if graph.snapshot.identity in fenced:
                    self.fenced_total += 1

                    try:
                        graph.fence()
                    except Exception as error:
                        self.denial_failed(error)

    def _lapsed(self, graphs: Sequence[DurableSession[T]]) -> tuple[StateIdentity, ...]:
        """An unreachable store fences nothing until the leases it stopped renewing lapse."""
        if asyncio.get_running_loop().time() - self.renewed < self.state_store.seconds:
            return ()

        return tuple(graph.snapshot.identity for graph in graphs)

    def begin_drain(self) -> None:
        assert isinstance(self.runtime, _DurableRuntime)
        self.runtime.draining = True

    def revoke(self, principal: str) -> None:
        try:
            self.backend.revoke(self.application, _principal_key(principal))
        except RecoveryUnavailable as error:
            self.store_available = False

            try:
                super().revoke(principal)
            except Exception as cleanup_error:
                error.add_note(f"local revocation cleanup also failed: {cleanup_error}")

            raise
        super().revoke(principal)

    async def close(self) -> None:
        self.begin_drain()

        try:
            await super().close()
        finally:
            if self.renewal is not None:
                self.renewal.cancel()
                await asyncio.gather(self.renewal, return_exceptions=True)
