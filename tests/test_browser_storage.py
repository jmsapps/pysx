"""Browser-local storage reads, writes, scoping, failure modes and cross-tab delivery."""

import asyncio
from typing import TYPE_CHECKING, cast

import pytest

from pysx import (
    Cookies,
    Fragment,
    StorageDecodeError,
    StorageError,
    StorageUnavailableError,
    Store,
    native,
)
from pysx.hosting.server import Session

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pysx.runtime.storage import StorageController
    from pysx.runtime.wire import JSONValue


def make_session(setup: Callable[[Store], object] | None = None) -> tuple[Session, Store]:
    """Build a session whose application creates a Store during its render."""

    stores: list[Store] = []

    def app() -> Fragment:
        store = Store()
        stores.append(store)

        if setup is not None:
            setup(store)

        return native.Div()

    return Session(app), stores[0]


def arg(message: dict[str, JSONValue], name: str) -> JSONValue:
    """Read one command argument, which the wire type models as an open JSON value."""

    return cast("dict[str, JSONValue]", message["args"])[name]


def answering(
    controller: StorageController, value: JSONValue, **extra: object
) -> Callable[[dict[str, JSONValue]], Awaitable[None]]:
    """Reply to every command with one canned result."""

    async def sender(message: dict[str, JSONValue]) -> None:
        controller.reply({"id": message["id"], "version": 1, "value": value, **extra})

    return sender


def test_browser_storage_requires_an_application_render() -> None:
    with pytest.raises(StorageError, match="inside an application render"):
        Store()


def test_browser_storage_read_write_and_remove_round_trip() -> None:
    async def check() -> None:
        session, store = make_session()
        controller = session.rendered.storage
        sent: list[dict[str, JSONValue]] = []

        async def sender(message: dict[str, JSONValue]) -> None:
            sent.append(message)
            controller.reply({"id": message["id"], "version": 1, "value": "dark"})

        controller.sender = sender
        assert await store.get("theme") == "dark"
        await store.set("theme", "light")
        await store.remove("theme")
        assert [message["op"] for message in sent] == ["get", "set", "remove"]
        assert [arg(message, "key") for message in sent] == ["pysx:app:theme"] * 3
        assert arg(sent[1], "value") == "light"
        assert not controller.pending
        session.dispose()

    asyncio.run(check())


def test_browser_storage_missing_key_reads_as_none() -> None:
    async def check() -> None:
        session, store = make_session()
        controller = session.rendered.storage
        controller.sender = answering(controller, None)
        assert await store.get("absent") is None
        assert await store.get_json("absent") is None
        session.dispose()

    asyncio.run(check())


def test_browser_storage_scopes_keys_to_the_declaring_application() -> None:
    scoped: list[Store] = []

    async def check() -> None:
        session, _store = make_session(lambda _store: scoped.append(Store("reports")))
        controller = session.rendered.storage
        sent: list[dict[str, JSONValue]] = []

        async def sender(message: dict[str, JSONValue]) -> None:
            sent.append(message)
            controller.reply({"id": message["id"], "version": 1, "value": None})

        controller.sender = sender
        await scoped[0].get("theme")
        assert arg(sent[0], "key") == "pysx:reports:theme"
        session.dispose()

    asyncio.run(check())


@pytest.mark.parametrize("key", ["", "a" * 129, "has space", "has:colon"])
def test_browser_storage_rejects_invalid_keys(key: str) -> None:
    async def check() -> None:
        session, store = make_session()
        controller = session.rendered.storage
        controller.sender = answering(controller, None)

        with pytest.raises(StorageError):
            await store.get(key)
        session.dispose()

    asyncio.run(check())


def test_browser_storage_rejects_oversized_values() -> None:
    async def check() -> None:
        session, store = make_session()
        controller = session.rendered.storage
        controller.sender = answering(controller, None)

        with pytest.raises(StorageError, match="at most"):
            await store.set("key", "x" * 8193)
        session.dispose()

    asyncio.run(check())


def test_browser_storage_unavailable_is_distinct_from_a_missing_key() -> None:
    async def check() -> None:
        session, store = make_session()
        controller = session.rendered.storage

        async def sender(message: dict[str, JSONValue]) -> None:
            controller.reply(
                {
                    "id": message["id"],
                    "version": 1,
                    "error": "browser storage is unavailable",
                    "unavailable": True,
                }
            )

        controller.sender = sender

        with pytest.raises(StorageUnavailableError):
            await store.get("theme")
        assert not controller.pending
        session.dispose()

    asyncio.run(check())


def test_browser_storage_json_round_trip_and_decode_failure() -> None:
    async def check() -> None:
        session, store = make_session()
        controller = session.rendered.storage
        written: list[JSONValue] = []

        async def sender(message: dict[str, JSONValue]) -> None:
            if message["op"] == "set":
                written.append(arg(message, "value"))
            controller.reply({"id": message["id"], "version": 1, "value": "{not json"})

        controller.sender = sender
        await store.set_json("layout", {"sidebar": "collapsed", "width": 240})
        assert written == ['{"sidebar": "collapsed", "width": 240}']

        with pytest.raises(StorageDecodeError):
            await store.get_json("layout")

        assert await store.get("layout") == "{not json"
        session.dispose()

    asyncio.run(check())


def test_browser_storage_set_json_refuses_none_and_unserialisable_values() -> None:
    async def check() -> None:
        session, store = make_session()
        controller = session.rendered.storage
        controller.sender = answering(controller, None)

        with pytest.raises(StorageError, match="already reads as None"):
            await store.set_json("key", None)

        with pytest.raises(StorageError, match="not JSON serialisable"):
            await store.set_json("key", {"bad": float("nan")})
        session.dispose()

    asyncio.run(check())


def test_browser_storage_without_a_browser_refuses_to_read() -> None:
    async def check() -> None:
        session, store = make_session()

        with pytest.raises(StorageError, match="connected browser"):
            await store.get("theme")
        session.dispose()

    asyncio.run(check())


def test_browser_storage_disconnect_cancels_outstanding_reads() -> None:
    async def check() -> None:
        session, store = make_session()
        controller = session.rendered.storage

        async def sender(_message: dict[str, JSONValue]) -> None:
            return None

        controller.sender = sender
        request = asyncio.create_task(store.get("theme"))
        await asyncio.sleep(0)
        controller.disconnect()

        with pytest.raises(StorageError, match="disconnected"):
            await request
        assert not controller.pending
        session.dispose()

    asyncio.run(check())


@pytest.mark.parametrize(
    "reply",
    [{"value": 42}, {"value": "x" * 8193}, {}],
)
def test_browser_storage_rejects_malformed_replies(reply: dict[str, object]) -> None:
    async def check() -> None:
        session, store = make_session()
        controller = session.rendered.storage

        async def sender(message: dict[str, JSONValue]) -> None:
            controller.reply({"id": message["id"], "version": 1, **reply})

        controller.sender = sender

        with pytest.raises(StorageError):
            await store.get("theme")
        session.dispose()

    asyncio.run(check())


def test_browser_storage_ignores_stale_and_mismatched_replies() -> None:
    async def check() -> None:
        session, store = make_session()
        controller = session.rendered.storage
        sent: list[dict[str, JSONValue]] = []

        async def sender(message: dict[str, JSONValue]) -> None:
            sent.append(message)

        controller.sender = sender
        request = asyncio.create_task(store.get("theme"))
        await asyncio.sleep(0)
        controller.reply({"id": "unknown", "version": 1, "value": "wrong"})
        controller.reply({"id": sent[0]["id"], "version": 2, "value": "wrong"})
        assert not request.done()
        controller.reply({"id": sent[0]["id"], "version": 1, "value": "right"})
        assert await request == "right"
        session.dispose()

    asyncio.run(check())


def test_browser_storage_cross_tab_notification_reaches_a_watcher() -> None:
    seen: list[str | None] = []

    async def check() -> None:
        session, _store = make_session(lambda store: store.watch("theme", seen.append))
        controller = session.rendered.storage
        controller.notify({"key": "pysx:app:theme", "value": "dark"})
        controller.notify({"key": "pysx:app:theme", "value": None})
        assert seen == ["dark", None]

        controller.notify({"key": "pysx:app:other", "value": "ignored"})
        controller.notify({"key": "pysx:app:theme", "value": 42})
        assert seen == ["dark", None]
        session.dispose()

    asyncio.run(check())


def test_browser_storage_teardown_releases_watchers() -> None:
    async def check() -> None:
        session, _store = make_session(lambda store: store.watch("theme", lambda _value: None))
        controller = session.rendered.storage
        assert controller.watchers
        session.dispose()
        assert not controller.watchers

    asyncio.run(check())


def make_cookie_session() -> tuple[Session, Cookies]:
    """Build a session whose application creates a browser Cookies handle."""

    handles: list[Cookies] = []

    def app() -> Fragment:
        handles.append(Cookies())

        return native.Div()

    return Session(app), handles[0]


def test_browser_cookies_require_an_application_render() -> None:
    with pytest.raises(StorageError, match="inside an application render"):
        Cookies()


def test_browser_cookies_read_write_and_remove_round_trip() -> None:
    async def check() -> None:
        session, cookies = make_cookie_session()
        controller = session.rendered.storage
        sent: list[dict[str, JSONValue]] = []

        async def sender(message: dict[str, JSONValue]) -> None:
            sent.append(message)
            controller.reply({"id": message["id"], "version": 1, "value": "dark"})

        controller.sender = sender
        assert await cookies.get("theme") == "dark"
        await cookies.set("theme", "light", max_age=600, path="/reports")
        await cookies.remove("theme", path="/reports")
        assert [message["op"] for message in sent] == [
            "cookie_get",
            "cookie_set",
            "cookie_remove",
        ]
        assert arg(sent[1], "value") == "light"
        assert arg(sent[1], "max_age") == 600
        assert arg(sent[1], "path") == "/reports"
        assert arg(sent[2], "path") == "/reports"
        session.dispose()

    asyncio.run(check())


def test_browser_cookies_report_an_unreadable_cookie_as_missing() -> None:
    """An HttpOnly cookie is invisible to scripts, so it reads as absent rather than failing."""

    async def check() -> None:
        session, cookies = make_cookie_session()
        controller = session.rendered.storage
        controller.sender = answering(controller, None)
        assert await cookies.get("session") is None
        session.dispose()

    asyncio.run(check())


WRITES: list[tuple[str, Callable[[Cookies], Awaitable[None]]]] = [
    ("reserved_name", lambda jar: jar.set("pysx_browser", "x")),
    ("invalid_name", lambda jar: jar.set("bad name", "x")),
    ("relative_path", lambda jar: jar.set("theme", "x", path="relative")),
    ("separator_in_path", lambda jar: jar.set("theme", "x", path="/a;b")),
    ("invalid_samesite", lambda jar: jar.set("theme", "x", samesite="Nope")),
    ("negative_max_age", lambda jar: jar.set("theme", "x", max_age=-5)),
]


@pytest.mark.parametrize(("_case", "write"), WRITES)
def test_browser_cookies_refuse_unsafe_arguments(
    _case: str, write: Callable[[Cookies], Awaitable[None]]
) -> None:
    """Reserved names, bad paths and contradictory attributes fail before the wire."""

    async def check() -> None:
        session, cookies = make_cookie_session()
        controller = session.rendered.storage
        controller.sender = answering(controller, None)

        with pytest.raises(StorageError):
            await write(cookies)
        session.dispose()

    asyncio.run(check())


def test_browser_cookies_reject_oversized_values() -> None:
    async def check() -> None:
        session, cookies = make_cookie_session()
        controller = session.rendered.storage
        controller.sender = answering(controller, None)

        with pytest.raises(StorageError, match="at most"):
            await cookies.set("theme", "x" * 4097)
        session.dispose()

    asyncio.run(check())


def test_browser_cookies_without_a_browser_refuse_to_read() -> None:
    async def check() -> None:
        session, cookies = make_cookie_session()

        with pytest.raises(StorageError, match="connected browser"):
            await cookies.get("theme")
        session.dispose()

    asyncio.run(check())
