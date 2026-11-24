"""Browser-local key/value storage an application reaches from Python.

The browser owns these values: they outlive the session, the server never holds them,
and the initial HTTP render cannot read them because no browser is attached yet. That
makes every operation here asynchronous and available only once a transport exists.

Keys are scoped to the declaring application. `localStorage` is a single per-origin
namespace, so two applications on one page would otherwise read and overwrite each
other's values, which is the cross-owner access the owned command channel exists to
prevent. The stored key is `pysx:<namespace>:<key>`.

Operations serialise to a small closed set the protocol validates. There is no
expression language and no raw-JavaScript escape hatch.
"""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from typing import TYPE_CHECKING, cast
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from pysx.runtime.wire import JSONValue

type JSONInput = bool | int | float | str | Sequence["JSONInput"] | Mapping[str, "JSONInput"] | None
"""What an application may hand to `set_json`.

`JSONValue` describes what the wire carries, and its `list`/`dict` members are
invariant, so an ordinary `list[str]` does not satisfy it. Accepting the covariant
`Sequence`/`Mapping` lets callers pass the collections they already have. A mapping
`json` cannot serialise still raises, and is reported as an unserialisable value.
"""

STORAGE_ROOT = "pysx"
MAX_KEY_LENGTH = 128
MAX_VALUE_LENGTH = 8192
MAX_PENDING = 64
COMMAND_TIMEOUT = 2
MAX_WATCHERS = 128
_NAMESPACE_LIMIT = 64


class StorageError(RuntimeError):
    """A storage operation failed, expired or lost its browser."""


class StorageUnavailableError(StorageError):
    """The browser refused storage, as private modes and exhausted quotas do."""


class StorageDecodeError(StorageError):
    """A stored value was not the JSON a typed read expected."""


MAX_COOKIE_VALUE = 4096


def _cookie_name(name: str) -> str:
    from pysx.hosting.request import MAX_COOKIE_NAME, RESERVED_COOKIES, CookieError

    if name in RESERVED_COOKIES:
        raise StorageError(f"{name} is reserved by the framework")

    try:
        from pysx.hosting.request import Cookie

        Cookie(name, "")
    except CookieError as error:
        raise StorageError(str(error)) from None

    if len(name) > MAX_COOKIE_NAME:
        raise StorageError("cookie name is too long")

    return name


def _cookie_path(path: str) -> str:
    if not path.startswith("/") or any(character in path for character in ";, \t\r\n"):
        raise StorageError("cookie path must be an absolute path without separators")

    return path


def _cookie_max_age(max_age: int | None) -> int | None:
    if max_age is not None and (type(max_age) is not int or not -1 <= max_age <= 34560000):
        raise StorageError("max_age must be an integer number of seconds")

    return max_age


def _cookie_samesite(samesite: str) -> str:
    if samesite not in ("Lax", "Strict", "None"):
        raise StorageError("samesite must be Lax, Strict or None")

    return samesite


def _key(value: str, limit: int, label: str) -> str:
    if type(value) is not str or not value or len(value) > limit:
        raise StorageError(f"{label} must be 1 to {limit} characters")

    if any(character.isspace() or character == ":" for character in value):
        raise StorageError(f"{label} must not contain whitespace or a colon")

    return value


class StorageController:
    """One application's outstanding storage work, scoped to its session."""

    def __init__(self) -> None:
        self.pending: dict[str, asyncio.Future[JSONValue]] = {}
        self.watchers: dict[str, list[Callable[[str | None], object]]] = {}
        self.sender: Callable[[dict[str, JSONValue]], Awaitable[None]] | None = None
        self.closed = False

    async def request(self, op: str, args: dict[str, JSONValue]) -> JSONValue:
        if self.closed:
            raise StorageError("session has been disposed")

        if self.sender is None:
            raise StorageError("browser storage requires a connected browser")

        if len(self.pending) >= MAX_PENDING:
            raise StorageError("too many pending storage commands")

        try:
            encoded = json.dumps(args, allow_nan=False)
        except ValueError as exc:
            raise StorageError("invalid command arguments") from exc

        if len(encoded) > MAX_VALUE_LENGTH * 2:
            raise StorageError("command arguments exceed limit")
        rid = uuid4().hex
        future: asyncio.Future[JSONValue] = asyncio.get_running_loop().create_future()
        self.pending[rid] = future

        try:
            async with asyncio.timeout(COMMAND_TIMEOUT):
                await self.sender({"t": "store", "version": 1, "id": rid, "op": op, "args": args})

                return await future
        except TimeoutError as exc:
            raise StorageError("storage command deadline exceeded") from exc
        finally:
            self.pending.pop(rid, None)

    def reply(self, message: dict[str, object]) -> None:
        rid = message.get("id")

        if not isinstance(rid, str) or message.get("version") != 1:
            return
        future = self.pending.get(rid)

        if future is None or future.done():
            return
        error = message.get("error")

        if isinstance(error, str):
            unavailable = message.get("unavailable") is True
            failure = StorageUnavailableError if unavailable else StorageError
            future.set_exception(failure(error[:256]))

            return

        if "value" not in message:
            future.set_exception(StorageError("malformed storage reply"))

            return
        value = message["value"]

        if value is not None and (type(value) is not str or len(value) > MAX_VALUE_LENGTH):
            future.set_exception(StorageError("invalid storage reply"))

            return
        future.set_result(cast("JSONValue", value))

    def notify(self, message: dict[str, object]) -> None:
        """Deliver a value another tab wrote. Dropped when nobody is watching that key."""

        key = message.get("key")
        value = message.get("value")

        if not isinstance(key, str) or (value is not None and type(value) is not str):
            return

        if value is not None and len(value) > MAX_VALUE_LENGTH:
            return

        for callback in tuple(self.watchers.get(key, ())):
            callback(value)

    def watch(self, key: str, callback: Callable[[str | None], object]) -> Callable[[], None]:
        if self.closed:
            raise StorageError("session has been disposed")
        registered = self.watchers.setdefault(key, [])

        if sum(len(entries) for entries in self.watchers.values()) >= MAX_WATCHERS:
            raise StorageError("storage watcher limit exceeded")
        registered.append(callback)

        def release() -> None:
            remaining = self.watchers.get(key)

            if remaining is None:
                return

            if callback in remaining:
                remaining.remove(callback)

            if not remaining:
                self.watchers.pop(key, None)

        return release

    def disconnect(self) -> None:
        """Cancel outstanding reads. Writes another tab makes meanwhile are not replayed."""

        self.sender = None

        for future in self.pending.values():
            if not future.done():
                future.set_exception(StorageError("browser disconnected"))
        self.pending.clear()

    def close(self) -> None:
        self.closed = True
        self.disconnect()
        self.watchers.clear()


storage_context: ContextVar[StorageController | None] = ContextVar("pysx_storage", default=None)


class Cookies:
    """Read and write cookies the browser lets scripts see.

    Separate from the response cookies an initial render sets: a script cannot change a
    response already sent, and an HttpOnly cookie is invisible here by design. A cookie
    written through this class reaches the server on the next request, which is what
    `localStorage` cannot do.
    """

    def __init__(self) -> None:
        controller = storage_context.get()

        if controller is None:
            raise StorageError("Cookies must be created inside an application render")
        self.controller = controller

    async def get(self, name: str) -> str | None:
        """Read one cookie. HttpOnly cookies read as missing, because scripts cannot see them."""

        value = await self.controller.request("cookie_get", {"key": _cookie_name(name)})

        return cast("str | None", value)

    async def set(
        self,
        name: str,
        value: str,
        *,
        path: str = "/",
        max_age: int | None = None,
        samesite: str = "Lax",
    ) -> None:
        if type(value) is not str or len(value) > MAX_COOKIE_VALUE:
            raise StorageError(f"value must be a string of at most {MAX_COOKIE_VALUE} characters")
        await self.controller.request(
            "cookie_set",
            {
                "key": _cookie_name(name),
                "value": value,
                "path": _cookie_path(path),
                "max_age": _cookie_max_age(max_age),
                "samesite": _cookie_samesite(samesite),
            },
        )

    async def remove(self, name: str, *, path: str = "/") -> None:
        """Expire one cookie. Removal identity is name plus path, as the browser defines it."""

        await self.controller.request(
            "cookie_remove", {"key": _cookie_name(name), "path": _cookie_path(path)}
        )


class Store:
    """Read and write this application's browser-local values.

    Created inside an application render. Every operation needs an attached browser,
    so restoration runs from `on_mount` through `own_task` rather than during the
    initial HTTP render.
    """

    def __init__(self, namespace: str = "app") -> None:
        controller = storage_context.get()

        if controller is None:
            raise StorageError("Store must be created inside an application render")
        self.controller = controller
        self.namespace = _key(namespace, _NAMESPACE_LIMIT, "namespace")

    def _scoped(self, key: str) -> str:
        return f"{STORAGE_ROOT}:{self.namespace}:{_key(key, MAX_KEY_LENGTH, 'key')}"

    async def get(self, key: str) -> str | None:
        value = await self.controller.request("get", {"key": self._scoped(key)})

        return cast("str | None", value)

    async def set(self, key: str, value: str) -> None:
        if type(value) is not str or len(value) > MAX_VALUE_LENGTH:
            raise StorageError(f"value must be a string of at most {MAX_VALUE_LENGTH} characters")
        await self.controller.request("set", {"key": self._scoped(key), "value": value})

    async def get_json(self, key: str) -> JSONValue:
        stored = await self.get(key)

        if stored is None:
            return None

        try:
            return cast("JSONValue", json.loads(stored))
        except ValueError as exc:
            raise StorageDecodeError(f"stored value for {key!r} is not valid JSON") from exc

    async def set_json(self, key: str, value: JSONInput) -> None:
        if value is None:
            raise StorageError("set_json cannot store None; a missing key already reads as None")

        try:
            encoded = json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise StorageError("value is not JSON serialisable") from exc
        await self.set(key, encoded)

    async def remove(self, key: str) -> None:
        await self.controller.request("remove", {"key": self._scoped(key)})

    def watch(self, key: str, callback: Callable[[str | None], object]) -> None:
        """Observe writes this key receives in other tabs of the same browser.

        The callback runs with the new value, or `None` when the key was removed.
        Notifications are live only while the transport is attached; writes made while
        it is down are dropped rather than replayed, so re-read on mount when exact
        agreement matters.
        """

        from pysx.components.lifecycle import on_cleanup

        release = self.controller.watch(self._scoped(key), callback)
        on_cleanup(release)
