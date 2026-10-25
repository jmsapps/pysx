"""Publication of one bounded browser checkpoint per live graph."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pysx.components.checkpoints import Restored, decode_snapshot, restore_values
from pysx.components.lifecycle import deferred_resources
from pysx.hosting.request import browser_id
from pysx.hosting.security import AccessDenied
from pysx.runtime.capsule import CapsuleContext, CapsuleError
from pysx.runtime.routing import Location
from pysx.runtime.transport import RecoveryBusy

if TYPE_CHECKING:
    from pysx.hosting.http import HTTPRuntime
    from pysx.hosting.request import RequestContext
    from pysx.runtime.capsule import CapsuleIssuer
    from pysx.runtime.session import Session


def route_binding(location: Location) -> str:
    """Bind capsules to the route an HTTP render can observe, which omits the fragment."""

    return location.path + location.search


@dataclass(slots=True, repr=False)
class BrowserCheckpoint:
    document: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    token: str | None = None
    revision: int = -1
    route: str = ""
    renew_at: float = 0
    attached: bool = False
    restored: bool = False
    reset: str = ""


class BrowserRecovery:
    def __init__(self, issuer: CapsuleIssuer, namespace: str) -> None:
        self.issuer = issuer
        self.namespace = namespace
        self.permits = asyncio.Semaphore(4)
        self.executing = 0
        self.waiting = 0

    def checkpoint(self, session: Session) -> BrowserCheckpoint:
        if session.browser_checkpoint is None:
            session.browser_checkpoint = BrowserCheckpoint()

        return session.browser_checkpoint

    def context(self, session: Session, request: RequestContext, document: str) -> CapsuleContext:
        return CapsuleContext(
            self.namespace,
            request.scheme + "://" + request.headers.get("host", ""),
            request.base_path,
            session.browser,
            request.principal,
            request.authentication_session,
            document,
            route_binding(session.routes.location),
        )

    def due(self, session: Session) -> bool:
        state = self.checkpoint(session)

        return (
            not state.attached
            or state.revision != session.rendered.scopes.checkpoints.revision
            or state.route != session.routes.location.url
            or time.monotonic() >= state.renew_at
        )

    def publish(
        self, session: Session, request: RequestContext, *, force: bool = False
    ) -> dict[str, object]:
        state = self.checkpoint(session)

        if not force and not self.due(session):
            return {}
        capture = session.rendered.scopes.checkpoints.capture()
        token = None
        reason = capture.reason or state.reset

        if capture.plaintext is not None:
            try:
                token = self.issuer.seal(
                    capture.plaintext,
                    self.context(session, request, state.document),
                    revision=session.transport.revision,
                )
            except CapsuleError:
                reason = "recovery context unavailable"
        state.token = token
        state.revision = session.rendered.scopes.checkpoints.revision
        state.route = session.routes.location.url
        jitter = int.from_bytes(hashlib.sha256(state.document.encode()).digest()[:2]) % 3601
        state.renew_at = time.monotonic() + 12 * 60 * 60 + jitter
        state.attached = True
        state.reset = ""

        return {"document": state.document, "capsule": token, "reset": reason}

    async def reconstruct(
        self,
        runtime: HTTPRuntime,
        request: RequestContext,
        token: str,
        document: str,
        location: str,
    ) -> Session:
        route = Location.parse(location)
        prefix = request.base_path

        if prefix and route.path != prefix and not route.path.startswith(prefix + "/"):
            raise AccessDenied("access denied")

        if request.access is not None:
            request.access("resume", route.url)
        context = CapsuleContext(
            self.namespace,
            request.scheme + "://" + request.headers.get("host", ""),
            prefix,
            browser_id(request),
            request.principal,
            request.authentication_session,
            document,
            route_binding(route),
        )
        # Authenticate before queueing, but retain no plaintext or decoded tree
        # while waiting. Admitted work opens again under its scratch budget.
        self.issuer.open(token, context)
        queued = self.permits.locked()

        if queued:
            if self.waiting >= 16:
                raise RecoveryBusy("recovery capacity")
            self.waiting += 1

        try:
            async with asyncio.timeout(runtime.limits.hello_timeout):
                await self.permits.acquire()
        except TimeoutError:
            raise RecoveryBusy("recovery deadline") from None
        finally:
            if queued:
                self.waiting -= 1
        self.executing += 1
        reserved = False
        session: Session | None = None
        deadline = time.monotonic() + runtime.limits.hello_timeout

        try:
            if request.access is not None:
                request.access("resume", route.url)
            original = decode_snapshot(self.issuer.open(token, context).state)

            if not runtime.reserve():
                raise RecoveryBusy("graph capacity")
            reserved = True
            excluded: set[str] = set()
            use_defaults = False

            for attempt in range(2):
                await asyncio.sleep(0)

                if request.access is not None:
                    request.access("resume", route.url)
                plan = Restored({} if use_defaults else original.entries, excluded=excluded)

                try:
                    with restore_values(plan), deferred_resources() as activate:
                        session = runtime.create_reserved(request, location=route.url)

                    if time.monotonic() >= deadline:
                        raise RecoveryBusy("recovery deadline")

                    if session.browser_candidate_failed or plan.ambiguous:
                        raise ValueError("incompatible recovered graph")
                    with session.context():
                        for callback in activate:
                            callback()

                    if time.monotonic() >= deadline:
                        raise RecoveryBusy("recovery deadline")
                    state = self.checkpoint(session)
                    state.document = document

                    if session.rendered.failures or session.rendered.unrepaired:
                        raise ValueError("recovery resource activation failed")
                    state.restored = bool(plan.applied)
                    state.reset = (
                        "incompatible state"
                        if use_defaults
                        else "some state reset"
                        if plan.resets or len(plan.applied) < len(original.entries)
                        else ""
                    )

                    return session
                except BaseException as error:
                    if session is not None:
                        runtime.release(session)
                        session = None

                    if (
                        not isinstance(error, Exception)
                        or isinstance(error, (AccessDenied, RecoveryBusy))
                        or attempt == 1
                    ):
                        raise

                    if plan.ambiguous:
                        excluded.update(plan.ambiguous)
                    else:
                        use_defaults = True

            raise ValueError("recovery construction unavailable")
        finally:
            if reserved:
                runtime.unreserve()
            self.executing -= 1
            self.permits.release()
