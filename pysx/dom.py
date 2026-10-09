"""Owned asynchronous DOM capabilities; never evaluates application JavaScript."""

from __future__ import annotations

import asyncio
import json
import math
import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .events import EventHandler
    from .wire import JSONValue

dom_context: ContextVar[DomController | None] = ContextVar("dom_controller", default=None)
type PropertyName = Literal["value", "checked", "selected", "disabled", "tabIndex", "textContent"]


class DomError(RuntimeError):
    """A command failed, expired or referred to a removed owner."""


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float


class DomController:
    def __init__(self) -> None:
        self.mounts: dict[str, tuple[str, bool]] = {}
        self.nodes: dict[str, str] = {}
        self.pending: dict[str, tuple[asyncio.Future[JSONValue], str]] = {}
        self.listeners: dict[str, tuple[EventHandler, str, str, str]] = {}
        self.sender: Callable[[dict[str, JSONValue]], Awaitable[None]] | None = None
        self.closed = False

    def mount(self, ref: DomRef, prefix: str) -> str:
        if ref.controller is not self:
            raise DomError("ref belongs to another render")

        if ref.token in self.mounts:
            raise DomError("a ref can mount on only one element")

        if len(self.nodes) >= 256:
            raise DomError("node limit exceeded")
        ref.token = uuid4().hex
        self.mounts[ref.token] = (prefix, ref.imperative)
        self.nodes[ref.token] = ref.token

        return ref.token

    def revoke(self, prefix: str) -> None:
        roots = {token for token, (owner, _zone) in self.mounts.items() if owner.startswith(prefix)}

        for root in roots:
            self.mounts.pop(root, None)

        for token, root in tuple(self.nodes.items()):
            if root in roots:
                self.nodes.pop(token, None)

        for hid, (_handler, _type, root, _target) in tuple(self.listeners.items()):
            if root in roots:
                self.listeners.pop(hid, None)

        for rid, (future, root) in tuple(self.pending.items()):
            if root in roots:
                self.pending.pop(rid, None)

                if not future.done():
                    future.set_exception(DomError("owner removed"))

    async def request(self, node: DomNode, op: str, args: dict[str, JSONValue]) -> JSONValue:
        if self.closed or node.root not in self.mounts or self.nodes.get(node.token) != node.root:
            raise DomError("stale or foreign DOM handle")

        if self.sender is None:
            raise DomError("DOM reads require a connected browser")

        if len(self.pending) >= 64:
            raise DomError("too many pending DOM commands")

        try:
            encoded = json.dumps(args, allow_nan=False)
        except ValueError as exc:
            raise DomError("invalid command arguments") from exc

        if len(encoded) > 8192:
            raise DomError("command arguments exceed limit")
        rid = uuid4().hex
        future: asyncio.Future[JSONValue] = asyncio.get_running_loop().create_future()
        self.pending[rid] = (future, node.root)

        try:
            async with asyncio.timeout(2):
                await self.sender(
                    {
                        "t": "dom",
                        "version": 1,
                        "id": rid,
                        "target": node.token,
                        "root": node.root,
                        "op": op,
                        "args": args,
                    }
                )

                return await future
        except TimeoutError as exc:
            raise DomError("DOM command deadline exceeded") from exc
        finally:
            self.pending.pop(rid, None)

    def reply(self, message: dict[str, object]) -> None:
        rid = message.get("id")

        if (
            not isinstance(rid, str)
            or type(message.get("version")) is not int
            or message.get("version") != 1
        ):
            return
        item = self.pending.get(rid)

        if item is None:
            return
        future, root = item

        if future.done() or root not in self.mounts:
            return
        error = message.get("error")

        if isinstance(error, str):
            future.set_exception(DomError(error[:256]))
        elif "value" in message:
            try:
                encoded = json.dumps(message["value"], allow_nan=False)
            except TypeError, ValueError:
                future.set_exception(DomError("invalid command reply"))

                return

            if len(encoded) > 16384:
                future.set_exception(DomError("command reply exceeds limit"))

                return
            future.set_result(cast("JSONValue", message["value"]))
        else:
            future.set_exception(DomError("malformed command reply"))
        revoked = message.get("revoked", [])

        if isinstance(revoked, list):
            for token in cast("list[object]", revoked)[:256]:
                if isinstance(token, str) and self.nodes.get(token) == root:
                    self.nodes.pop(token, None)
                    self.mounts.pop(token, None)

                    for hid, (_handler, _type, _root, target) in tuple(self.listeners.items()):
                        if target == token:
                            self.listeners.pop(hid, None)

    def close(self) -> None:
        self.closed = True
        self.revoke("")
        self.sender = None


class Dom:
    """Create ref markers inside an application render."""

    def __init__(self) -> None:
        controller = dom_context.get()

        if controller is None:
            raise DomError("Dom must be created inside an application render")
        self.controller = controller

    def ref(self, *, imperative: bool = False) -> DomRef:
        return DomRef(self.controller, imperative)


class DomRef:
    def __init__(self, controller: DomController, imperative: bool) -> None:
        self.controller = controller
        self.imperative = imperative
        self.token = ""

    def handle(self) -> DomNode:
        """Capture this mount. Retained handles never follow replacement elements."""

        if self.token not in self.controller.mounts:
            raise DomError("ref is not mounted")

        return DomNode(self.controller, self.token, self.token)


@dataclass(frozen=True)
class DomNode:
    controller: DomController
    token: str
    root: str

    async def _request(self, op: str, **args: JSONValue) -> JSONValue:
        return await self.controller.request(self, op, args)

    async def focus(self, *, prevent_scroll: bool = False) -> None:
        await self._request("focus", prevent_scroll=prevent_scroll)

    async def blur(self) -> None:
        await self._request("blur")

    async def active_element(self) -> DomNode | None:
        return self._node(await self._request("active"))

    def _node(self, value: JSONValue) -> DomNode | None:
        if value is None:
            return None

        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
            raise DomError("invalid node result")

        if len(self.controller.nodes) >= 256 and value not in self.controller.nodes:
            raise DomError("node limit exceeded")
        self.controller.nodes[value] = self.root

        return DomNode(self.controller, value, self.root)

    async def query(self, selector: str) -> DomNode | None:
        if len(selector) > 256:
            raise DomError("selector exceeds limit")

        return self._node(await self._request("query", selector=selector))

    async def get_by_id(self, element_id: str) -> DomNode | None:
        return self._node(await self._request("get_by_id", id=element_id))

    async def get_attribute(self, name: str) -> str | None:
        value = await self._request("get_attr", name=name)

        if value is not None and not isinstance(value, str):
            raise DomError("invalid attribute result")

        return value

    async def set_attribute(self, name: str, value: str | None) -> None:
        await self._request("set_attr", name=name, value=value)

    async def get_property(self, name: PropertyName) -> str | bool | int | float:
        value = await self._request("get_prop", name=name)

        if type(value) not in {str, bool, int, float}:
            raise DomError("invalid property result")

        return cast("str | bool | int | float", value)

    async def set_property(self, name: PropertyName, value: str | bool | int | float) -> None:
        await self._request("set_prop", name=name, value=value)

    async def node_property(
        self, name: Literal["parentNode", "firstChild", "nextSibling"]
    ) -> DomNode | None:
        return self._node(await self._request("node_prop", name=name))

    async def style(self, name: str, value: str | None) -> None:
        await self._request("style", name=name, value=value)

    async def measure(self) -> Rect:
        value = await self._request("measure")

        if (
            not isinstance(value, list)
            or len(value) != 4
            or any(type(v) not in {int, float} for v in value)
        ):
            raise DomError("invalid measurement result")

        numbers = cast("list[float]", value)

        if not all(math.isfinite(number) for number in numbers):
            raise DomError("nonfinite measurement")

        return Rect(*numbers)

    async def selection(
        self, start: int, end: int, direction: Literal["forward", "backward", "none"] = "none"
    ) -> None:
        await self._request("selection", start=start, end=end, direction=direction)

    async def read_selection(self) -> tuple[int, int, str]:
        value = await self._request("read_selection")

        if (
            not isinstance(value, list)
            or len(value) != 3
            or type(value[0]) is not int
            or type(value[1]) is not int
            or not isinstance(value[2], str)
        ):
            raise DomError("invalid selection result")

        return value[0], value[1], value[2]

    async def scroll(self, x: float, y: float) -> None:
        await self._request("scroll", x=x, y=y)

    async def create_element(
        self, tag: str, *, namespace: Literal["html", "svg", "math"] = "html"
    ) -> DomNode:
        node = self._node(await self._request("create", tag=tag, namespace=namespace))

        if node is None:
            raise DomError("missing created node")

        return node

    async def create_text(self, value: str) -> DomNode:
        node = self._node(await self._request("create_text", value=value))

        if node is None:
            raise DomError("missing text node")

        return node

    async def create_fragment(self) -> DomNode:
        node = self._node(await self._request("create_fragment"))

        if node is None:
            raise DomError("missing fragment")

        return node

    async def append(self, child: DomNode) -> None:
        await self.insert(child)

    async def insert(self, child: DomNode, before: DomNode | None = None) -> None:
        if (
            child.controller is not self.controller
            or child.root != self.root
            or (before is not None and before.root != self.root)
        ):
            raise DomError("cannot move nodes across owners")
        await self._request("insert", child=child.token, before=before.token if before else None)

    async def remove(self) -> None:
        await self._request("remove")
        self.controller.nodes.pop(self.token, None)

        for hid, (_handler, _type, _root, target) in tuple(self.controller.listeners.items()):
            if target == self.token:
                self.controller.listeners.pop(hid, None)

    async def listen(
        self, event_type: str, handler: EventHandler, *, window: bool = False
    ) -> DomListener:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", event_type):
            raise DomError("invalid listener event type")
        event_type = handler.event_type or event_type

        if len(self.controller.listeners) >= 128:
            raise DomError("listener limit exceeded")
        hid = uuid4().hex
        self.controller.listeners[hid] = (handler, event_type, self.root, self.token)

        try:
            policy = cast("dict[str, JSONValue]", handler.policy(event_type))
            await self._request("listen", h=hid, policy=policy, window=window)
        except BaseException:
            self.controller.listeners.pop(hid, None)

            raise

        return DomListener(self, hid)


@dataclass(frozen=True)
class DomListener:
    node: DomNode
    handler: str

    async def close(self) -> None:
        self.node.controller.listeners.pop(self.handler, None)
        await self.node.controller.request(self.node, "unlisten", {"h": self.handler})
