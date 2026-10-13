"""Bounded HTTP renders and single-use adoption of their live graphs."""

from __future__ import annotations

import asyncio
import html
import json
import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .forms import PayloadError
from .render_tree import Tree, apply, snapshot
from .session import Session

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
        capacity: int = 128,
        ttl: float = 30,
        development: bool = False,
        mutation_capacity: int = 64,
        task_capacity: int = 1024,
    ) -> None:
        if capacity < 1 or ttl <= 0:
            raise ValueError("render capacity and TTL must be positive")
        self.app = app
        self.capacity = capacity
        self.ttl = ttl
        self.development = development
        self.mutation_capacity = mutation_capacity
        self.task_capacity = task_capacity
        self.pending: dict[str, PendingRender] = {}

    def expire(self, token: str) -> None:
        pending = self.pending.pop(token, None)

        if pending is not None:
            pending.expiry.cancel()
            pending.session.dispose()

    def close(self) -> None:
        errors: list[Exception] = []

        for token in tuple(self.pending):
            try:
                self.expire(token)
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

        if len(self.pending) >= self.capacity:
            return HTTPResult(503, headers, b"render capacity reached")
        session = Session(
            self.app,
            location=request.path,
            development=self.development,
            request=request,
            mutation_capacity=self.mutation_capacity,
            task_capacity=self.task_capacity,
        )

        try:
            document = request.document
            path = urlsplit(request.path).path
            pattern = session.routes.routers[0].response.pattern if session.routes.routers else None

            if (
                path != request.base_path + "/index.html"
                and "." in path.rsplit("/", 1)[-1]
                and (pattern is None or pattern.rsplit("/", 1)[-1] == "*")
            ):
                session.dispose()

                return HTTPResult(404, headers, b"not found")
            navigations = session.routes.navigations()
            redirect = document.redirect or (navigations[-1]["url"] if navigations else None)

            if redirect is not None:
                if "\r" in redirect or "\n" in redirect:
                    raise ValueError("invalid redirect")
                session.dispose()

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
            session.dispose()

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
