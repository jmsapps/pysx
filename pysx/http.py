"""Bounded HTTP renders and single-use adoption of their live graphs."""

from __future__ import annotations

import asyncio
import html
import json
import math
import re
import secrets
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .forms import PayloadError
from .render_tree import Tree, apply, snapshot
from .session import Session
from .transport import TransportLimits

if TYPE_CHECKING:
    from collections.abc import Callable
    from string.templatelib import Template

    from .render import Fragment
    from .request import RequestContext


@dataclass(frozen=True)
class HTTPResult:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    adoption: str | None = None


@dataclass
class PendingRender:
    session: Session
    browser: str
    principal: str | None
    expiry: asyncio.TimerHandle


@dataclass
class ResumeHandle:
    session: Session
    browser: str
    principal: str | None
    deadline: float | None = None
    expiry: asyncio.TimerHandle | None = None


def _controls(node: Tree) -> None:
    """Emit browser defaults that otherwise require client-side hydration."""

    if node.tag == "textarea" and "value" in node.attrs:
        value = node.attrs["value"] or ""
        node.children = [Tree("#text", text=("\n" if value.startswith("\n") else "") + value)]

    if node.tag == "select":
        selected = node.attrs.get("data-pysx-selected")
        values = json.loads(selected) if selected is not None else [node.attrs.get("value")]

        def options(child: Tree) -> None:
            if child.tag == "option":
                value = child.attrs.get("value")

                if value is None:
                    def text(item: Tree) -> str:
                        return (
                            item.text
                            if item.tag == "#text"
                            else "".join(text(entry) for entry in item.children)
                        )

                    value = re.sub(r"[\t\n\f\r ]+", " ", text(child)).strip(" ")

                if value in values:
                    child.attrs["selected"] = None
                elif "value" in node.attrs or selected is not None:
                    child.attrs.pop("selected", None)

            for item in child.children:
                options(item)

        options(node)

    for child in node.children:
        _controls(child)


class HTTPRuntime:
    def __init__(
        self,
        app: Callable[[], Template | Fragment],
        *,
        capacity: int = 100,
        ttl: float = 30,
        development: bool = False,
        mutation_capacity: int = 64,
        task_capacity: int = 1024,
        limits: TransportLimits | None = None,
    ) -> None:
        if (
            type(capacity) is not int
            or capacity < 1
            or type(ttl) not in (int, float)
            or not math.isfinite(ttl)
            or ttl <= 0
        ):
            raise ValueError("render capacity and TTL must be positive")
        self.app = app
        self.capacity = capacity
        self.ttl = ttl
        self.development = development
        self.mutation_capacity = mutation_capacity
        self.task_capacity = task_capacity
        self.pending: dict[str, PendingRender] = {}
        self.limits = limits or TransportLimits()
        self.graphs: set[Session] = set()
        self.reservations = 0
        self.closed = False
        self.cleanup_tasks: set[asyncio.Task[object]] = set()
        self.resume_handles: dict[str, ResumeHandle] = {}
        self.detached: OrderedDict[str, ResumeHandle] = OrderedDict()

    def attach(self, session: Session, request: RequestContext) -> str:
        from .request import browser_id

        token = secrets.token_urlsafe(32)
        self.resume_handles[token] = ResumeHandle(session, browser_id(request), request.principal)
        session.detached = False

        return token

    def reap(self, now: float | None = None) -> None:
        instant = asyncio.get_running_loop().time() if now is None else now

        for handle in tuple(self.detached.values()):
            if handle.deadline is not None and handle.deadline <= instant:
                self.release(handle.session)

    def detach(self, session: Session) -> None:
        self.reap()

        for token, handle in self.resume_handles.items():
            if handle.session is session:
                session.detached = True
                session.pending.clear()
                session.routes.commands.clear()
                session.rendered.failures.clear()
                session.rendered.dom.disconnect()
                handle.deadline = asyncio.get_running_loop().time() + self.limits.resume_ttl
                handle.expiry = asyncio.get_running_loop().call_later(
                    self.limits.resume_ttl, self.reap
                )
                self.detached[token] = handle

                break

        while len(self.detached) > self.limits.detached:
            self.release(next(iter(self.detached.values())).session)

    def resume(self, token: str, request: RequestContext, epoch: str, revision: int) -> Session:
        from .request import browser_id

        self.reap()
        handle = self.detached.get(token)

        if (
            self.closed
            or handle is None
            or handle.browser != browser_id(request)
            or handle.principal != request.principal
            or handle.session.transport.epoch != epoch
            or revision > handle.session.transport.revision
            or handle.session.rendered.unrepaired
            or handle.session.pressured
        ):
            raise ValueError("unknown or mismatched resume handle")
        self.detached.pop(token)
        self.resume_handles.pop(token)

        if handle.expiry is not None:
            handle.expiry.cancel()
        handle.session.detached = False
        handle.session.pending.clear()
        handle.session.rendered.failures.clear()
        handle.session.routes.commands.clear()

        return handle.session

    def reserve(self) -> bool:
        self.graphs = {session for session in self.graphs if not session.closed}

        if self.closed or len(self.graphs) + self.reservations >= self.limits.graphs:
            return False
        self.reservations += 1

        return True

    def unreserve(self) -> None:
        if self.reservations < 1:
            raise RuntimeError("unbalanced graph reservation")
        self.reservations -= 1

    def create_reserved(self, request: RequestContext, *, location: str | None = None) -> Session:
        session = Session(
            self.app,
            location=location or request.path,
            request=request,
            development=self.development,
            mutation_capacity=self.mutation_capacity,
            task_capacity=self.task_capacity,
            outbox_bytes=self.limits.outbox_bytes,
        )
        self.graphs.add(session)

        return session

    def tasks(self) -> set[asyncio.Task[object]]:
        tasks = self.cleanup_tasks | {
            task for session in self.graphs for task in session.runtime.tasks
        }
        tasks.update(
            session.runtime.worker for session in self.graphs if session.runtime.worker is not None
        )

        return tasks

    def release(self, session: Session) -> None:
        for token, handle in tuple(self.resume_handles.items()):
            if handle.session is session:
                self.resume_handles.pop(token)
                self.detached.pop(token, None)

                if handle.expiry is not None:
                    handle.expiry.cancel()
        tasks = set(session.runtime.tasks)

        if session.runtime.worker is not None:
            tasks.add(session.runtime.worker)
        self.cleanup_tasks.update(tasks)

        for task in tasks:
            task.add_done_callback(self.cleanup_tasks.discard)
        self.graphs.discard(session)
        session.dispose()

    def expire(self, token: str) -> None:
        pending = self.pending.pop(token, None)

        if pending is not None:
            pending.expiry.cancel()
            self.release(pending.session)

    def close(self) -> None:
        self.closed = True
        errors: list[Exception] = []

        for token in tuple(self.pending):
            try:
                self.expire(token)
            except Exception as error:
                errors.append(error)

        for session in tuple(self.graphs):
            try:
                self.release(session)
            except Exception as error:
                errors.append(error)

        if errors:
            raise ExceptionGroup("pending render cleanup failed", errors)

    def render(
        self, request: RequestContext, browser: str, *, client_path: str = "/client.js"
    ) -> HTTPResult:
        headers = (
            ("content-type", "text/html; charset=utf-8"),
            ("cache-control", "private, no-store"),
        )

        if len(self.pending) >= self.capacity or not self.reserve():
            return HTTPResult(503, headers, b"render capacity reached")

        try:
            session = self.create_reserved(request)
        finally:
            self.unreserve()

        try:
            document = request.document
            path = urlsplit(request.path).path
            pattern = session.routes.routers[0].response.pattern if session.routes.routers else None

            if (
                path != request.base_path + "/index.html"
                and "." in path.rsplit("/", 1)[-1]
                and (pattern is None or pattern.rsplit("/", 1)[-1] == "*")
            ):
                self.release(session)

                return HTTPResult(404, headers, b"not found")
            navigations = session.routes.navigations()
            redirect = document.redirect or (navigations[-1]["url"] if navigations else None)

            if redirect is not None:
                if "\r" in redirect or "\n" in redirect:
                    raise ValueError("invalid redirect")
                self.release(session)

                return HTTPResult(document.status or 303, (*headers, ("location", redirect)), b"")
            tree = snapshot(session.rendered.body, unwrap_row=False)
            apply(tree, session.pending)
            _controls(tree)
            session.pending.clear()
            body = tree.markup()
            css = session.rendered.css.replace("<", r"\3c ")
            description = html.escape(document.description, quote=True)
            client_url = html.escape(client_path, quote=True)
            token = secrets.token_urlsafe(32)
            content = (
                '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                f"<title>{html.escape(document.title)}</title>"
                f'<meta name="description" content="{description}">'
                f'<style id="pysx-style">{css}</style></head><body>'
                f'<div id="pysx-root" data-pysx-adoption="{token}">{body}</div>'
                f'<script src="{client_url}" defer></script></body></html>'
            ).encode()
            status = document.status or (
                session.routes.routers[0].response.status if session.routes.routers else 200
            )
            expiry = asyncio.get_running_loop().call_later(self.ttl, self.expire, token)
            self.pending[token] = PendingRender(session, browser, request.principal, expiry)

            return HTTPResult(status, headers, content, token)
        except BaseException:
            self.release(session)

            raise

    def claim(self, token: str, request: RequestContext, browser: str) -> Session:
        pending = self.pending.get(token)

        if pending is None or pending.browser != browser or pending.principal != request.principal:
            raise ValueError("unknown or mismatched adoption handle")
        del self.pending[token]
        pending.expiry.cancel()

        return pending.session


def reconcile(session: Session, edits: list[tuple[str, object, int | None]]) -> None:
    """Validate every edit before a single serialized, handler-free adoption turn."""

    if len(edits) > 1024 or len({hid for hid, _, _ in edits}) != len(edits):
        raise PayloadError("invalid adoption edits")

    for hid, value, _ in edits:
        target = session.rendered.bindings.get(hid)

        if target is None:
            raise PayloadError("unknown adoption binding")
        target.validate(value)
    prior, session.pending = session.pending, []
    with session.context():
        ops = session.dispatch("", None, edits=edits)
    session.pending = prior + ops
