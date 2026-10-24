"""Production checkpoint protection, host keys and trusted login-session binding."""

import asyncio
import base64
import importlib.util
import json
import re
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, cast

import browser_capture_fixture as capture_fixture
import browser_recovery_measurements as measurements
import browser_restore_fixture as restore_fixture
import pytest
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV
from test_connection_pump import FakeConnection
from test_state_recovery import CODEC, view

from pysx import (
    AccessDenied,
    Application,
    AuthenticatedIdentity,
    DurableApplication,
    HostConfig,
    Route,
    Router,
    SQLiteOwnershipStore,
    native,
)
from pysx.components.checkpoints import (
    MAX_BYTES,
    MAX_NODES,
    Encoder,
    Limit,
    Unsupported,
    decode_snapshot,
    restore_values,
    sites,
)
from pysx.hosting import browser_recovery as recovery_module
from pysx.hosting import recovery_keys
from pysx.hosting.asgi import ASGI
from pysx.hosting.browser_recovery import BrowserRecovery
from pysx.hosting.hosting import request_from_headers
from pysx.hosting.http import HTTPRuntime
from pysx.hosting.recovery_keys import decode_ring, encode_ring, load_keys
from pysx.runtime import capsule
from pysx.runtime.capsule import (
    LIFETIME,
    MAX_CAPSULE,
    MAX_PLAINTEXT,
    SKEW,
    CapsuleContext,
    CapsuleError,
    CapsuleIssuer,
    KeyRing,
    RootKey,
)
from pysx.runtime.connection import serve_connection
from pysx.runtime.forms import PayloadError
from pysx.runtime.session import Session
from pysx.runtime.transport import RecoveryBusy
from pysx.runtime.wire import parse_message

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysx import Fragment
    from pysx.components.checkpoints import Restored
    from pysx.hosting.asgi import Event
    from pysx.hosting.request import RequestContext
    from pysx.runtime.routing import RouteState


def context() -> CapsuleContext:
    return CapsuleContext(
        "app:main",
        "https://app.test",
        "/app",
        "browser",
        "alice",
        "login-1",
        "document",
        "/app?q=1#section",
    )


def issuer() -> CapsuleIssuer:
    return CapsuleIssuer(KeyRing(RootKey(b"key-id01", b"x" * 32)))


@pytest.mark.parametrize(
    ("plaintext", "expected"),
    [
        ("", "07f5f4169bbf55a8400cd47ea6fd400f"),
        ("0100000000000000", "c2ef328e5c71c83b843122130f7364b761e0b97427e3df28"),
    ],
)
def test_capsule_rfc8452_known_answers(plaintext: str, expected: str) -> None:
    # RFC 8452 appendix C.2, AES-256-GCM-SIV.
    cipher = AESGCMSIV(bytes.fromhex("01" + "00" * 31))
    nonce = bytes.fromhex("03" + "00" * 11)
    assert cipher.encrypt(nonce, bytes.fromhex(plaintext), b"") == bytes.fromhex(expected)
    assert cipher.decrypt(nonce, bytes.fromhex(expected), b"") == bytes.fromhex(plaintext)


def test_capsule_independent_workers_and_limits() -> None:
    first = issuer()
    state = b"x" * (MAX_PLAINTEXT - 24)
    token = first.seal(state, context(), revision=17, now=1000)
    assert len(token) <= MAX_CAPSULE
    other = CapsuleIssuer(decode_ring(encode_ring(first.ring)))
    restored = other.open(token, context(), now=1001)
    assert restored.state == state
    assert restored.revision == 17
    assert restored.issued_at == 1000
    assert "alice" not in token
    with pytest.raises(CapsuleError, match="too large"):
        first.seal(state + b"x", context(), revision=18, now=1000)

    for bad in ("x" * (MAX_CAPSULE + 1), "!" * 200, "ü" * 200, token + "=", ""):
        with pytest.raises(CapsuleError):
            first.open(bad, context(), now=1001)


@pytest.mark.parametrize(
    "field",
    [
        "namespace",
        "origin",
        "base_path",
        "browser",
        "principal",
        "authentication_session",
        "document",
        "route",
    ],
)
def test_capsule_context_mismatch(field: str) -> None:
    crypto = issuer()
    token = crypto.seal(b"secret", context(), revision=0, now=1000)
    with pytest.raises(CapsuleError, match="invalid recovery capsule"):
        crypto.open(token, replace(context(), **{field: "different"}), now=1000)


def test_capsule_tamper_expiry_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    crypto = issuer()
    token = crypto.seal(b"secret", context(), revision=0, now=1000)
    raw = base64.urlsafe_b64decode(token)

    for index in (0, 1, 9, 41, 53, len(raw) - 1):
        changed = raw[:index] + bytes([raw[index] ^ 1]) + raw[index + 1 :]
        with pytest.raises(CapsuleError):
            crypto.open(base64.urlsafe_b64encode(changed).decode(), context(), now=1000)
    crypto.open(token, context(), now=1000 + LIFETIME + SKEW)

    for now in (1000 - SKEW - 1, 1000 + LIFETIME + SKEW + 1):
        with pytest.raises(CapsuleError, match="expired"):
            crypto.open(token, context(), now=now)
    root = crypto.ring.issuing
    rotated = CapsuleIssuer(KeyRing(RootKey(b"key-id02", b"y" * 32), replace(root, retire_at=1000)))
    assert rotated.open(token, context(), now=1001).state == b"secret"
    with pytest.raises(CapsuleError, match="key unavailable"):
        rotated.open(token, context(), now=1000 + LIFETIME + SKEW + 1)
    monkeypatch.setattr(crypto, "_count", 2**32)
    second = crypto.seal(b"second", context(), revision=1, now=1000)
    assert base64.urlsafe_b64decode(second)[9:41] != raw[9:41]
    assert rotated.open(second, context(), now=1000).state == b"second"


def test_capsule_unsupported_crypto_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(_key: bytes) -> AESGCMSIV:
        raise UnsupportedAlgorithm("unavailable")

    monkeypatch.setattr(capsule, "AESGCMSIV", unavailable)
    with pytest.raises(UnsupportedAlgorithm):
        issuer()


def test_keys_concurrent_first_start_and_mount(tmp_path: Path) -> None:
    directory = tmp_path / "private"

    def start(_index: int) -> KeyRing:
        return load_keys(directory, provision=True)

    with ThreadPoolExecutor(max_workers=16) as pool:
        rings = list(pool.map(start, range(64)))
    assert all(ring == rings[0] for ring in rings)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "recovery.keys").stat().st_mode) == 0o600
    assert list(directory.iterdir()) == [directory / "recovery.keys"]
    assert load_keys(directory) == rings[0]
    assert "x" * 32 not in repr(rings[0])


@pytest.mark.parametrize(
    "kind", ["symlink", "directory", "public", "oversize", "fifo", "parent_symlink"]
)
def test_keys_reject_unsafe_files(tmp_path: Path, kind: str) -> None:
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    key = directory / "recovery.keys"

    if kind == "parent_symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(directory, target_is_directory=True)
        directory = alias
    elif kind == "symlink":
        key.symlink_to(tmp_path / "missing")
    elif kind == "directory":
        key.mkdir()
    elif kind == "fifo":
        import os

        os.mkfifo(key, 0o600)
    else:
        key.write_bytes(b"x" * (93 if kind == "oversize" else 44))
        key.chmod(0o644 if kind == "public" else 0o600)
    with pytest.raises((ValueError, OSError)):
        load_keys(directory)


def test_keys_host_startup_and_no_import_provisioning(tmp_path: Path) -> None:
    directory = tmp_path / "mount"
    ring = load_keys(directory, provision=True)
    app = Application(
        lambda: native.P("hello"), config=HostConfig(recovery_key_directory=directory)
    )
    assert app.recovery_issuer is None

    async def exercise() -> None:
        await app.start()
        assert app.recovery_issuer is not None
        assert app.recovery_issuer.ring == ring
        await app.close()

    asyncio.run(exercise())
    missing = Application(
        lambda: native.P("hello"), config=HostConfig(recovery_key_directory=tmp_path / "absent")
    )
    with pytest.raises(FileNotFoundError):
        asyncio.run(missing.start())


def test_keys_automatic_host_ownership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "automatic"

    def location(_namespace: str) -> Path:
        return directory

    monkeypatch.setattr(recovery_keys, "default_directory", location)
    app = Application(lambda: native.P("hello"))
    assert not directory.exists()

    async def exercise() -> None:
        await app.start()
        assert app.recovery_issuer is not None
        assert app.recovery_issuer.ring == load_keys(directory)
        await app.close()

    asyncio.run(exercise())


def test_keys_default_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", "relative")
    assert recovery_keys.default_directory("app").parent == tmp_path / ".local" / "state" / "pysx"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert recovery_keys.default_directory("app").parent == tmp_path / "xdg" / "pysx"
    assert recovery_keys.default_directory("other") != recovery_keys.default_directory("app")


def test_auth_binding_logout_and_same_principal_relogin() -> None:
    identity: AuthenticatedIdentity | str | None = AuthenticatedIdentity("alice", "login-1")
    app = Application(lambda: native.P("hello"), config=HostConfig(principal=lambda _: identity))
    request = request_from_headers("/", [("host", "localhost")])
    prepared = app.prepare(request)
    assert prepared.principal == "alice"
    assert prepared.authentication_session == "login-1"
    assert prepared.access is not None
    prepared.access("event", "")
    identity = AuthenticatedIdentity("alice", "login-2")
    with pytest.raises(AccessDenied):
        prepared.access("event", "")
    identity = None
    with pytest.raises(AccessDenied):
        prepared.access("event", "")
    identity = "alice"
    legacy = app.prepare(request)
    assert legacy.authentication_session is None
    assert legacy.access is not None
    legacy.access("event", "")
    with pytest.raises(CapsuleError, match="authentication session unavailable"):
        issuer().seal(b"state", replace(context(), authentication_session=None), revision=0)


def test_auth_binding_asgi_trusted_state() -> None:
    adapter = ASGI(Application(lambda: native.P("hello")))
    scope: Event = {
        "path": "/",
        "headers": [(b"host", b"localhost")],
        "state": {"pysx_principal": "alice", "pysx_authentication_session": "login-1"},
    }
    request = adapter._request(scope)  # pyright: ignore[reportPrivateUsage]
    assert request.principal == "alice"
    assert request.authentication_session == "login-1"
    assert "login-1" not in repr(request)
    scope["state"] = {"pysx_principal": "alice", "pysx_authentication_session": 1}
    with pytest.raises(ValueError, match="authentication session"):
        adapter._request(scope)  # pyright: ignore[reportPrivateUsage]


def test_capture_ordinary_native_dsl_hidden_and_unchanged() -> None:
    for app in (capture_fixture.native_app, capture_fixture.dsl_app, capture_fixture.CallableApp()):
        capture_fixture.held.clear()
        session = Session(app)
        registry = session.rendered.scopes.checkpoints
        initial = registry.capture()
        assert initial.plaintext is not None
        assert initial.resets == 0
        assert initial.slots == 2  # Global/shared and derived values are excluded.
        assert registry.capture() is initial
        metadata_calls = sites.cache_info()
        with session.context():
            capture_fixture.held["count"].set(17)
            capture_fixture.held["draft"].set("unsaved draft")
        changed = registry.capture()
        assert changed is not initial
        assert changed.plaintext is not None
        assert b"17" in changed.plaintext
        assert b"unsaved draft" in changed.plaintext
        assert registry.capture() is changed
        assert sites.cache_info() == metadata_calls
        assert sites.cache_info().maxsize == 128
        session.dispose()
        assert not registry.slots
        assert registry.cached is None


def test_capture_owned_copy_types_and_initializers() -> None:
    original: dict[str, object] = {"nested": [1, "two", None, True, 1.25]}
    session = Session(lambda: capture_fixture.native_app(original))
    original["nested"] = "external mutation"
    captured = session.rendered.scopes.checkpoints.capture()
    assert captured.plaintext is not None
    assert b"external mutation" not in captured.plaintext
    assert b"two" in captured.plaintext
    first_marks = {slot.mark for slot in session.rendered.scopes.checkpoints.slots.values()}
    other = Session(lambda: capture_fixture.native_app({"nested": [2]}))
    assert {slot.mark for slot in other.rendered.scopes.checkpoints.slots.values()} != first_marks
    session.dispose()
    other.dispose()


def test_capture_keyed_owners_removed_scopes_and_ambiguity() -> None:
    session = Session(capture_fixture.keyed_app)
    registry = session.rendered.scopes.checkpoints
    snapshot = registry.capture()
    assert snapshot.plaintext is not None
    # Tuple-valued row source resets independently; each row's string remains supported.
    assert snapshot.resets == 1
    assert b"first" in snapshot.plaintext
    assert b"second" in snapshot.plaintext
    with session.context():
        capture_fixture.held["a"].set("edited first")
        capture_fixture.held["rows"].set([("b", "second")])
    removed = registry.capture()
    assert removed.plaintext is not None
    assert b"edited first" not in removed.plaintext
    assert len(registry.slots) == 2
    session.dispose()
    repeated = Session(capture_fixture.repeated_app)
    result = repeated.rendered.scopes.checkpoints.capture()
    assert result.resets == 3
    assert result.plaintext is not None
    assert b"ambiguous call site" in result.plaintext
    repeated.dispose()


@pytest.mark.parametrize("value", [(1, 2), {1, 2}, float("nan"), 2**63, {1: "bad key"}])
def test_capture_unsupported_slot_keeps_other_values(value: object) -> None:
    session = Session(lambda: capture_fixture.native_app(value))
    result = session.rendered.scopes.checkpoints.capture()
    assert result.plaintext is not None
    assert result.resets == 1
    assert result.slots == 2
    assert result.nodes == 13  # Root, two record arrays and five fields in each.
    session.dispose()


def test_capture_budgets_stop_before_copying_and_clear_previous() -> None:
    session = Session(capture_fixture.native_app)
    registry = session.rendered.scopes.checkpoints
    assert registry.capture().plaintext is not None
    with session.context():
        capture_fixture.held["draft"].set("x" * 1_000_000)
    result = registry.capture()
    assert result.plaintext is None
    assert result.reason == "snapshot byte limit"
    assert result.nodes < 20
    session.dispose()
    encoder = Encoder()
    with pytest.raises(Limit):
        encoder.value(list(range(100_000)))
    assert encoder.nodes <= MAX_NODES + 1
    assert encoder.size <= MAX_BYTES

    for value in ("é" * 20_000, {"large": "\\" * 30_000}):
        with pytest.raises(Limit):
            Encoder().value(value)


def test_capture_alias_cycle_and_depth_reset() -> None:
    shared: list[object] = [1]
    cycle: list[object] = []
    cycle.append(cycle)
    deep: object = 0

    for _ in range(18):
        deep = [deep]

    for value in ([shared, shared], cycle, deep):
        with pytest.raises(Unsupported):
            Encoder().value(value)


def test_capture_source_unavailable_and_metadata_limit() -> None:
    namespace: dict[str, object] = {}
    exec(
        "from pysx import signal, native\ndef app():\n"
        "    value = signal(1)\n    return native.P(value)",
        namespace,
    )

    session = Session(cast("Callable[[], Fragment]", namespace["app"]))
    result = session.rendered.scopes.checkpoints.capture()
    assert result.resets == 1
    assert result.plaintext is not None
    assert b"source unavailable" in result.plaintext
    session.dispose()
    namespace = {}
    exec(
        "from pysx import signal, native\ndef app():\n"
        "    values = [signal(i) for i in range(1000)]\n    return native.P('live')",
        namespace,
    )
    session = Session(cast("Callable[[], Fragment]", namespace["app"]))
    registry = session.rendered.scopes.checkpoints
    assert len(registry.slots) <= 512
    assert registry.metadata_bytes <= 128 * 1024
    assert registry.capture().plaintext is None
    session.dispose()
    assert not registry.slots


def test_capture_never_invokes_custom_value_hooks() -> None:
    class Hostile(type):
        def __getattribute__(cls, key: str) -> object:
            raise AssertionError(key)

    class Opaque(metaclass=Hostile):
        def __str__(self) -> str:
            raise AssertionError("conversion hook")

    with pytest.raises(Unsupported, match="unsupported value type"):
        Encoder().value(Opaque())


def test_capture_portable_source_and_initializer_changes(tmp_path: Path) -> None:
    source = (
        "from pysx import signal, native\ndef app():\n"
        "    count = signal(0)\n    return native.P(count)\n"
    )

    def build(directory: str, text: str) -> Session:
        root = tmp_path / directory
        root.mkdir()
        filename = root / "view.py"
        filename.write_text(text)
        spec = importlib.util.spec_from_file_location("portable_capture", filename)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        return Session(module.app)

    first = build("first", source)
    moved = build("moved", "\n# title-only edit\n" + source)
    changed = build("changed", source.replace("signal(0)", "signal(1)"))
    first_result = first.rendered.scopes.checkpoints.capture()
    moved_result = moved.rendered.scopes.checkpoints.capture()
    assert first_result.plaintext == moved_result.plaintext
    assert first_result.plaintext is not None
    assert str(tmp_path).encode() not in first_result.plaintext
    first_rows = json.loads(first_result.plaintext)
    changed_result = changed.rendered.scopes.checkpoints.capture()
    assert changed_result.plaintext is not None
    changed_rows = json.loads(changed_result.plaintext)
    assert first_rows[0][0] == changed_rows[0][0]
    assert first_rows[0][1] != changed_rows[0][1]

    for session in (first, moved, changed):
        session.dispose()


@pytest.mark.parametrize("version", [3, 4])
def test_publication_wire_versions_and_bounds(version: int) -> None:
    message: dict[str, object] = {"t": "hello", "version": version}

    if version == 4:
        message["document"] = "a" * 32
        message["capsule"] = "b" * (36 * 1024)
    assert parse_message(json.dumps(message), version=version) == message
    with pytest.raises(PayloadError):
        parse_message(json.dumps(message), version=7 - version)

    for field, value in (
        ("document", "bad"),
        ("capsule", "x" * (36 * 1024 + 1)),
        ("capsule", "bad+token"),
        ("capsule", None),
    ):
        with pytest.raises(PayloadError):
            parse_message(json.dumps({**message, field: value}), version=version)

    if version == 4:
        assert (
            parse_message(json.dumps({**message, "capsule": "YQ=="}), version=4)["capsule"]
            == "YQ=="
        )
        with pytest.raises(PayloadError, match="hello size"):
            parse_message(
                json.dumps({**message, "capsule": "x", "edits": ["x" * (48 * 1024)]}), version=4
            )


def test_publication_cached_capture_overflow_and_renewal() -> None:
    request = request_from_headers("/", [("host", "app.test"), ("cookie", "pysx_browser=b1")])
    session = Session(capture_fixture.native_app, request=request)
    service = BrowserRecovery(issuer(), "test:app")
    first = service.publish(session, request)
    token = cast("str", first["capsule"])
    document = cast("str", first["document"])
    assert service.issuer.open(token, service.context(session, request, document)).state
    snapshot = session.rendered.scopes.checkpoints.cached
    assert not service.due(session)
    assert service.publish(session, request) == {}
    state = service.checkpoint(session)
    state.renew_at = 0
    renewed = service.publish(session, request)
    assert renewed["capsule"] != token
    assert session.rendered.scopes.checkpoints.cached is snapshot
    assert state.token == renewed["capsule"]
    capture_fixture.held["draft"].set("x" * (24 * 1024))
    overflow = service.publish(session, request)
    assert overflow["capsule"] is None
    assert overflow["reset"]
    assert state.token is None
    session.dispose()


def test_publication_http_adoption_and_hidden_turn() -> None:
    async def exercise() -> None:
        runtime = HTTPRuntime(capture_fixture.native_app)
        runtime.browser_recovery = BrowserRecovery(issuer(), "test:app")
        request = request_from_headers("/", [("host", "app.test"), ("cookie", "pysx_browser=b1")])
        response = runtime.render(request, "b1")
        assert b'data-pysx-protocol="4"' in response.body
        found = re.search(rb'data-pysx-document="([\w-]+)"', response.body)
        assert found is not None
        document = found[1].decode()
        assert response.adoption is not None
        session = runtime.pending[response.adoption].session
        binding = next(iter(session.rendered.bindings))
        connection = FakeConnection(
            "/ws?protocol=4&adoption=" + response.adoption,
            [
                json.dumps(
                    {
                        "t": "hello",
                        "version": 4,
                        "document": document,
                        "edits": [{"h": binding, "v": "before attachment"}],
                    }
                )
            ],
        )
        connection.request = replace(connection.request, headers=request.headers)
        pump = asyncio.create_task(serve_connection(runtime, connection))

        try:
            async with asyncio.timeout(3):
                while not any("capsule" in message for message in connection.sent):
                    await asyncio.sleep(0.001)
            published = [frame for frame in connection.sent if "capsule" in frame][-1]
            opened = runtime.browser_recovery.issuer.open(
                cast("str", published["capsule"]),
                runtime.browser_recovery.context(session, request, document),
            )
            assert b"before attachment" in opened.state
            previous = published["capsule"]
            # Hidden writes still complete a serialized turn and publish a checkpoint.
            async with session.runtime.turn():
                session.rendered.scopes.checkpoints.changed()
            async with asyncio.timeout(3):
                while not connection.frames("checkpoint"):
                    await asyncio.sleep(0.001)
            assert connection.frames("checkpoint")[-1]["capsule"] != previous
            assert all(frame["version"] == 4 for frame in connection.sent)
            before = len(connection.sent)
            async with session.runtime.turn():
                session.routes.observe("/next")
                session.routes.commands.append(
                    {"t": "navigation", "url": "/next", "rev": 0, "mode": "push"}
                )
            async with asyncio.timeout(3):
                while not any("navigations" in frame for frame in connection.sent[before:]):
                    await asyncio.sleep(0.001)
            navigated = next(frame for frame in connection.sent[before:] if "navigations" in frame)
            assert navigated["capsule"]
            runtime.browser_recovery.issuer.open(
                cast("str", navigated["capsule"]),
                runtime.browser_recovery.context(session, request, document),
            )
            state = runtime.browser_recovery.checkpoint(session)
            checkpoints = len(connection.frames("checkpoint"))
            state.renew_at = 0
            session.changed.set()
            async with asyncio.timeout(3):
                while len(connection.frames("checkpoint")) <= checkpoints:
                    await asyncio.sleep(0.001)
            # Queue repair ahead of the pending-state push while holding its turn.
            async with session.runtime.turn():
                connection.incoming.put_nowait(
                    json.dumps(
                        {
                            "t": "repair",
                            "version": 4,
                            "epoch": session.transport.epoch,
                            "state_rev": session.transport.revision,
                        }
                    )
                )
                await asyncio.sleep(0)
                with session.context():
                    capture_fixture.held["count"].set(7)
            async with asyncio.timeout(3):
                while not connection.frames("repair"):
                    await asyncio.sleep(0.001)
            repair = connection.frames("repair")[-1]
            assert "7" in cast("str", repair["html"])
            assert repair["capsule"]
            assert not session.pending
            runtime.browser_recovery.issuer.open(
                cast("str", repair["capsule"]),
                runtime.browser_recovery.context(session, request, document),
            )
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            runtime.close()

    asyncio.run(exercise())


def test_publication_wrong_document_does_not_consume_graph() -> None:
    async def exercise() -> None:
        runtime = HTTPRuntime(capture_fixture.native_app)
        service = BrowserRecovery(issuer(), "test:app")
        runtime.browser_recovery = service
        request = request_from_headers("/", [("host", "app.test"), ("cookie", "pysx_browser=b1")])
        response = runtime.render(request, "b1")
        assert response.adoption is not None
        session = runtime.pending[response.adoption].session
        document = service.checkpoint(session).document
        with pytest.raises(ValueError, match="adoption"):
            runtime.claim(response.adoption, request, "b1", document="z" * 32)
        assert runtime.pending[response.adoption].session is session
        assert runtime.claim(response.adoption, request, "b1", document=document) is session
        token = runtime.attach(session, request)
        runtime.detach(session)
        with pytest.raises(ValueError, match="resume"):
            runtime.resume(token, request, session.transport.epoch, 0, document="z" * 32)
        assert token in runtime.detached
        assert not session.closed
        assert (
            runtime.resume(token, request, session.transport.epoch, 0, document=document) is session
        )
        runtime.close()

    asyncio.run(exercise())


def test_publication_resumed_route_and_capsule_share_repair() -> None:
    def page(_state: RouteState) -> Fragment:
        return native.P("route")

    def app() -> Fragment:
        return Router([Route("*", page)]).view()

    async def exercise() -> None:
        runtime = HTTPRuntime(app)
        service = BrowserRecovery(issuer(), "test:app")
        runtime.browser_recovery = service
        request = request_from_headers("/", [("host", "app.test"), ("cookie", "pysx_browser=b1")])
        session = runtime.create_reserved(request)
        checkpoint = service.publish(session, request)
        document = cast("str", checkpoint["document"])
        token = runtime.attach(session, request)
        runtime.detach(session)
        async with session.runtime.turn():
            session.routes.observe("/next")
        connection = FakeConnection(
            "/ws?protocol=4&resume=1&location=%2F",
            [
                json.dumps(
                    {
                        "t": "hello",
                        "version": 4,
                        "document": document,
                        "capsule": checkpoint["capsule"],
                        "resume": token,
                        "epoch": session.transport.epoch,
                        "state_rev": 0,
                    }
                )
            ],
        )
        connection.request = replace(connection.request, headers=request.headers)
        pump = asyncio.create_task(serve_connection(runtime, connection))

        try:
            async with asyncio.timeout(3):
                while not connection.frames("repair"):
                    await asyncio.sleep(0.001)
            repair = connection.frames("repair")[0]
            assert repair["navigations"] == [
                {"t": "navigation", "url": "/next", "rev": 0, "mode": "replace"}
            ]
            assert not connection.frames("navigation")
            service.issuer.open(
                cast("str", repair["capsule"]), service.context(session, request, document)
            )
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            runtime.close()

    asyncio.run(exercise())


def recovery_request() -> RequestContext:
    return request_from_headers("/", [("host", "app.test"), ("cookie", "pysx_browser=b1")])


async def close_runtime(runtime: HTTPRuntime) -> None:
    runtime.close()
    await asyncio.gather(*runtime.tasks(), return_exceptions=True)


def test_reconstruction_independent_graphs_and_managed_resources() -> None:
    async def exercise() -> None:
        request = recovery_request()
        first = HTTPRuntime(restore_fixture.app)
        service = BrowserRecovery(issuer(), "test:app")
        original = first.create_reserved(request)
        restore_fixture.held["count"].set(9)
        restore_fixture.held["draft"].set("private draft")
        published = service.publish(original, request)
        token = cast("str", published["capsule"])
        document = cast("str", published["document"])
        epoch = original.transport.epoch
        await close_runtime(first)
        restore_fixture.events.clear()
        second = HTTPRuntime(restore_fixture.app)
        other_worker = BrowserRecovery(issuer(), "test:app")

        try:
            restored = await other_worker.reconstruct(second, request, token, document, "/")
            assert restored.transport.epoch != epoch
            assert restore_fixture.held["count"]() == 9
            assert restore_fixture.held["draft"]() == "private draft"
            first_count = restore_fixture.held["count"]
            assert other_worker.checkpoint(restored).restored
            forked = await other_worker.reconstruct(second, request, token, document, "/")
            assert forked is not restored
            assert forked.transport.epoch != restored.transport.epoch
            restore_fixture.held["count"].set(10)
            assert first_count() == 9
            async with asyncio.timeout(3):
                while (
                    restore_fixture.events.count("load") < 2
                    or restore_fixture.events.count("task") < 2
                ):
                    await asyncio.sleep(0.001)

            for name in ("effect", "subscription", "setup", "load", "task"):
                assert restore_fixture.events.count(name) == 2
            assert not other_worker.executing
            assert not other_worker.waiting
            assert not second.reservations
        finally:
            await close_runtime(second)
        assert restore_fixture.events.count("unsubscribe") == 2
        assert restore_fixture.events.count("cleanup") == 2
        assert not second.graphs
        assert not second.resume_handles

    asyncio.run(exercise())


def test_reconstruction_initializer_change_resets_only_incompatible_slot() -> None:
    async def exercise() -> None:
        request = recovery_request()
        runtime = HTTPRuntime(capture_fixture.native_app)
        service = BrowserRecovery(issuer(), "test:app")
        original = runtime.create_reserved(request)
        capture_fixture.held["count"].set(8)
        capture_fixture.held["draft"].set("preserved")
        published = service.publish(original, request)
        await close_runtime(runtime)
        changed = HTTPRuntime(partial(capture_fixture.native_app, initial=1))

        try:
            restored = await service.reconstruct(
                changed,
                request,
                cast("str", published["capsule"]),
                cast("str", published["document"]),
                "/",
            )
            assert capture_fixture.held["count"]() == 1
            assert capture_fixture.held["draft"]() == "preserved"
            assert service.checkpoint(restored).reset == "some state reset"
        finally:
            await close_runtime(changed)

    asyncio.run(exercise())


def test_reconstruction_failed_candidate_defers_resources_and_retries_defaults() -> None:
    async def exercise() -> None:
        request = recovery_request()
        service = BrowserRecovery(issuer(), "test:app")
        runtime = HTTPRuntime(restore_fixture.app)
        original = runtime.create_reserved(request)
        restore_fixture.held["count"].set(13)
        published = service.publish(original, request)
        await close_runtime(runtime)
        restore_fixture.events.clear()
        restore_fixture.fail_restored = True
        runtime = HTTPRuntime(restore_fixture.app)

        try:
            restored = await service.reconstruct(
                runtime,
                request,
                cast("str", published["capsule"]),
                cast("str", published["document"]),
                "/",
            )
            assert restore_fixture.held["count"]() == 0
            state = service.checkpoint(restored)
            assert not state.restored
            assert state.reset == "incompatible state"
            async with asyncio.timeout(3):
                while "load" not in restore_fixture.events or "task" not in restore_fixture.events:
                    await asyncio.sleep(0.001)
            assert restore_fixture.events.count("setup") == 2
            assert restore_fixture.events.count("cleanup") == 1

            for name in ("effect", "subscription", "load", "task"):
                assert restore_fixture.events.count(name) == 1
            assert len(runtime.graphs) == 1
            assert not runtime.reservations
        finally:
            restore_fixture.fail_restored = False
            await close_runtime(runtime)
        assert restore_fixture.events.count("cleanup") == 2
        assert restore_fixture.events.count("unsubscribe") == 1

    asyncio.run(exercise())


def test_reconstruction_faults_reject_before_setup() -> None:
    async def exercise() -> None:
        request = replace(recovery_request(), principal="alice", authentication_session="login-1")
        service = BrowserRecovery(issuer(), "test:app")
        original = Session(capture_fixture.native_app, request=request)
        published = service.publish(original, request)
        original.dispose()
        calls: list[int] = []

        def forbidden() -> Fragment:
            calls.append(1)

            return native.P("fresh")

        runtime = HTTPRuntime(forbidden)
        token = cast("str", published["capsule"])
        document = cast("str", published["document"])

        for changed, location in (
            (replace(request, authentication_session="login-2"), "/"),
            (replace(request, principal="bob"), "/"),
            (replace(request, principal=None, authentication_session=None), "/"),
            (replace(request, scheme="https"), "/"),
            (request, "/different"),
        ):
            with pytest.raises(CapsuleError):
                await service.reconstruct(runtime, changed, token, document, location)
        with pytest.raises(CapsuleError):
            await service.reconstruct(runtime, request, token[:-4] + "AAAA", document, "/")

        def deny(_action: str, _target: str) -> None:
            raise AccessDenied("denied")

        with pytest.raises(AccessDenied):
            await service.reconstruct(runtime, replace(request, access=deny), token, document, "/")
        assert calls == []
        assert not runtime.graphs
        assert not runtime.reservations
        assert service.executing == service.waiting == 0
        runtime.close()

    asyncio.run(exercise())


def test_reconstruction_faults_bounded_admission_and_cleanup() -> None:
    async def exercise() -> None:
        request = recovery_request()
        original = Session(capture_fixture.native_app, request=request)
        service = BrowserRecovery(issuer(), "test:app")
        published = service.publish(original, request)
        original.dispose()
        runtime = HTTPRuntime(capture_fixture.native_app)

        try:
            results = await asyncio.gather(
                *(
                    service.reconstruct(
                        runtime,
                        request,
                        cast("str", published["capsule"]),
                        cast("str", published["document"]),
                        "/",
                    )
                    for _ in range(30)
                ),
                return_exceptions=True,
            )
            assert sum(isinstance(result, Session) for result in results) == 20
            assert sum(isinstance(result, RecoveryBusy) for result in results) == 10
            assert len(runtime.graphs) == 20
            assert not runtime.reservations
            assert not service.executing
            assert not service.waiting
        finally:
            await close_runtime(runtime)
        assert not runtime.graphs
        assert not runtime.resume_handles
        assert not runtime.detached

    asyncio.run(exercise())


def test_reconstruction_faults_snapshot_decoder() -> None:
    session = Session(capture_fixture.native_app)
    captured = session.rendered.scopes.checkpoints.capture()
    assert captured.plaintext is not None
    rows = json.loads(captured.plaintext)
    assert decode_snapshot(captured.plaintext).entries

    invalid: tuple[object, ...] = (
        [rows[0], rows[0]],
        [["bad", "", "str", "value", ""]],
        {},
        [None],
    )

    for value in invalid:
        with pytest.raises(ValueError, match="recovery"):
            decode_snapshot(json.dumps(value).encode())

    for raw in (b"x" * (MAX_BYTES + 1), b"[" * 2000 + b"]" * 2000, b"[NaN]"):
        with pytest.raises(ValueError, match="recovery"):
            decode_snapshot(raw)
    session.dispose()


def test_reconstruction_faults_queue_timeout_and_cancellation() -> None:
    async def exercise() -> None:
        request = recovery_request()
        service = BrowserRecovery(issuer(), "test:app")
        session = Session(capture_fixture.native_app, request=request)
        published = service.publish(session, request)
        session.dispose()
        runtime = HTTPRuntime(capture_fixture.native_app)
        runtime.limits = replace(runtime.limits, hello_timeout=0.02)

        for _ in range(4):
            await service.permits.acquire()
        token = cast("str", published["capsule"])
        document = cast("str", published["document"])

        try:
            with pytest.raises(RecoveryBusy, match="deadline"):
                await service.reconstruct(runtime, request, token, document, "/")
            task = asyncio.create_task(service.reconstruct(runtime, request, token, document, "/"))
            async with asyncio.timeout(1):
                while not service.waiting:
                    await asyncio.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert not runtime.graphs
            assert not runtime.reservations
            assert service.waiting == service.executing == 0
        finally:
            for _ in range(4):
                service.permits.release()
        task = asyncio.create_task(service.reconstruct(runtime, request, token, document, "/"))
        async with asyncio.timeout(1):
            while not service.executing:
                await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert service.executing == service.waiting == 0
        assert not runtime.reservations
        assert not runtime.graphs
        await close_runtime(runtime)

    asyncio.run(exercise())


def test_reconstruction_login_generation_guards_adoption_and_live_resume() -> None:
    async def exercise() -> None:
        request = replace(recovery_request(), principal="alice", authentication_session="login-1")
        changed = replace(request, authentication_session="login-2")
        runtime = HTTPRuntime(capture_fixture.native_app)
        response = runtime.render(request, "b1")
        assert response.adoption is not None
        with pytest.raises(ValueError, match="adoption"):
            runtime.claim(response.adoption, changed, "b1")
        session = runtime.claim(response.adoption, request, "b1")
        token = runtime.attach(session, request)
        runtime.detach(session)
        with pytest.raises(ValueError, match="resume"):
            runtime.resume(token, changed, session.transport.epoch, 0)
        assert token in runtime.detached
        assert not session.closed
        assert runtime.resume(token, request, session.transport.epoch, 0) is session
        await close_runtime(runtime)

    asyncio.run(exercise())


def test_reconstruction_anonymous_reset_requires_same_origin_and_rotates_binder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def directory(_namespace: str) -> Path:
        return tmp_path / "keys"

    monkeypatch.setattr(recovery_keys, "default_directory", directory)

    async def exercise() -> None:
        host = Application(capture_fixture.native_app)
        request = request_from_headers("/", [("host", "localhost"), ("cookie", "pysx_browser=b1")])
        response = await host.http(request)
        assert response.status == 200
        assert response.adoption is not None
        graph = host.runtime.pending[response.adoption].session
        base_headers = [("host", "localhost"), ("cookie", "pysx_browser=b1")]

        for extra in (
            [],
            [("origin", "http://localhost")],
            [("origin", "http://evil.test"), ("x-pysx-reset", "1")],
        ):
            denied = await host.http(
                request_from_headers("/__pysx__/reset", base_headers + extra), method="POST"
            )
            assert denied.status == 403
            assert not graph.closed
        reset = await host.http(
            request_from_headers(
                "/__pysx__/reset",
                [*base_headers, ("origin", "http://localhost"), ("x-pysx-reset", "1")],
            ),
            method="POST",
        )
        assert reset.status == 204
        assert graph.closed
        cookie = dict(reset.headers)["set-cookie"]
        assert "HttpOnly" in cookie
        assert not cookie.startswith("pysx_browser=b1;")
        assert not host.runtime.graphs
        assert not host.runtime.pending
        await host.close()

    asyncio.run(exercise())


def test_reconstruction_new_callsite_ambiguity_retries_without_reassigning_state() -> None:
    async def exercise() -> None:
        request = recovery_request()
        service = BrowserRecovery(issuer(), "test:app")
        original = Session(restore_fixture.repeated_app, request=request)
        restore_fixture.held["repeat-first"].set(8)
        restore_fixture.held["stable"].set("kept")
        published = service.publish(original, request)
        original.dispose()
        restore_fixture.events.clear()
        restore_fixture.duplicate_site = True
        runtime = HTTPRuntime(restore_fixture.repeated_app)

        try:
            restored = await service.reconstruct(
                runtime,
                request,
                cast("str", published["capsule"]),
                cast("str", published["document"]),
                "/",
            )
            assert restore_fixture.held["repeat-first"]() == 0
            assert restore_fixture.held["repeat-second"]() == 0
            assert restore_fixture.held["stable"]() == "kept"
            assert restore_fixture.events.count("repeated-setup") == 2
            assert service.checkpoint(restored).reset == "some state reset"
            assert len(runtime.graphs) == 1
        finally:
            restore_fixture.duplicate_site = False
            await close_runtime(runtime)

    asyncio.run(exercise())


def test_reconstruction_moved_source_root(tmp_path: Path) -> None:
    source = (
        "from pysx import native, signal\ndef app():\n"
        "    count = signal(0)\n    return native.P(count)\n"
    )

    def build(directory: str, content: str) -> Session:
        path = tmp_path / directory / "portable.py"
        path.parent.mkdir()
        path.write_text(content)
        spec = importlib.util.spec_from_file_location("portable_restore", path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        return Session(module.app)

    first = build("first", source)
    slot = next(iter(first.rendered.scopes.checkpoints.slots.values()))
    slot.signal.set(27)
    captured = first.rendered.scopes.checkpoints.capture()
    assert captured.plaintext is not None
    first.dispose()
    plan = decode_snapshot(captured.plaintext)
    with restore_values(plan):
        moved = build("second", "# compatible deployment at a different path\n" + source)
    assert len(plan.applied) == 1
    assert next(iter(moved.rendered.scopes.checkpoints.slots.values())).signal() == 27
    moved.dispose()


def test_reconstruction_http_graph_lost_before_adoption_uses_existing_hello() -> None:
    async def exercise() -> None:
        request = recovery_request()
        first = HTTPRuntime(capture_fixture.native_app)
        service = BrowserRecovery(issuer(), "test:app")
        first.browser_recovery = service
        response = first.render(request, "b1")
        assert response.adoption is not None
        graph = first.pending[response.adoption].session
        state = service.checkpoint(graph)
        token, document, epoch = state.token, state.document, graph.transport.epoch
        await close_runtime(first)
        second = HTTPRuntime(capture_fixture.native_app)
        second.browser_recovery = BrowserRecovery(issuer(), "test:app")
        connection = FakeConnection(
            "/ws?protocol=4&adoption=" + response.adoption,
            [
                json.dumps(
                    {
                        "t": "hello",
                        "version": 4,
                        "document": document,
                        "capsule": token,
                        "edits": [],
                    }
                )
            ],
        )
        connection.request = replace(connection.request, headers=request.headers)
        pump = asyncio.create_task(serve_connection(second, connection))

        try:
            async with asyncio.timeout(3):
                while not connection.frames("init"):
                    await asyncio.sleep(0.001)
            restored = connection.frames("init")[0]
            assert restored["recovered"] is True
            assert restored["epoch"] != epoch
            assert restored["capsule"]
            assert connection.closed is None
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await close_runtime(second)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("suite", "sessions"), [("unknown", "1000"), ("local", "0"), ("local", "999")]
)
def test_scale_contract_cli_rejects_empty_or_incomplete_profiles(
    suite: str,
    sessions: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys, "argv", ["measure", "--suite", suite, "--sessions", sessions, "--report", str(output)]
    )
    with pytest.raises(SystemExit) as stopped:
        measurements.main()
    assert stopped.value.code == 2
    assert not output.exists()


def test_scale_contract_production_history_has_no_retained_ownership(tmp_path: Path) -> None:
    result = asyncio.run(measurements.history(8, tmp_path))
    assert result["issued_and_expired"] == 8
    assert result["fixed_live_graphs"] == 4
    assert result["churn_turns"] == 200
    assert result["max_observed_graphs"] == 5
    assert result["historical_ownership_after_expiry"] == 0
    assert result["key_files"] == ["recovery.keys"]
    assert result["key_bytes"] == 44
    assert cast("int", result["max_recovery_owned_bytes"]) < 256 * 1024
    assert cast("int", result["capture_scratch_peak_bytes"]) < 2 * 1024 * 1024


def test_scale_contract_missing_worker_result_cannot_emit_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(_count: int, _directory: Path) -> dict[str, object]:
        raise RuntimeError("worker verification unavailable")

    output = tmp_path / "report.json"
    monkeypatch.setattr(measurements, "measure", unavailable)
    monkeypatch.setattr(
        sys, "argv", ["measure", "--suite", "local", "--sessions", "1000", "--report", str(output)]
    )
    with pytest.raises(RuntimeError, match="worker verification unavailable"):
        measurements.main()
    assert not output.exists()


def test_explicit_durable_startup_does_not_require_default_browser_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_namespace: str) -> Path:
        raise AssertionError("default browser key storage should be unused")

    monkeypatch.setattr(recovery_keys, "default_directory", unavailable)
    host = DurableApplication(
        view,
        initial=lambda: 0,
        codec=CODEC,
        store=SQLiteOwnershipStore(tmp_path / "state.db"),
        application="explicit",
    )

    async def exercise() -> None:
        try:
            await host.start()
            assert host.recovery_issuer is None
            assert host.runtime.browser_recovery is None
        finally:
            await host.close()

    asyncio.run(exercise())


def test_reconstruction_waiters_retain_no_decoded_state_and_recheck_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoded: list[int] = []

    def decoding(plaintext: bytes) -> Restored:
        decoded.append(len(plaintext))

        return decode_snapshot(plaintext)

    monkeypatch.setattr(recovery_module, "decode_snapshot", decoding)

    async def exercise() -> None:
        request = recovery_request()
        service = BrowserRecovery(issuer(), "test:app")
        original = Session(capture_fixture.native_app, request=request)
        published = service.publish(original, request)
        original.dispose()
        revoked = False

        def current(_action: str, _target: str) -> None:
            if revoked:
                raise AccessDenied("access denied")

        request = replace(request, access=current)
        runtime = HTTPRuntime(capture_fixture.native_app)

        for _ in range(4):
            await service.permits.acquire()
        released = 0
        task = asyncio.create_task(
            service.reconstruct(
                runtime,
                request,
                cast("str", published["capsule"]),
                cast("str", published["document"]),
                "/",
            )
        )

        try:
            async with asyncio.timeout(1):
                while not service.waiting:
                    await asyncio.sleep(0)
            assert decoded == []
            revoked = True
            service.permits.release()
            released = 1
            with pytest.raises(AccessDenied, match="access denied"):
                await task
            assert decoded == []
            assert not runtime.graphs
            assert not runtime.reservations
            assert service.executing == service.waiting == 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

            for _ in range(4 - released):
                service.permits.release()
            await close_runtime(runtime)

    asyncio.run(exercise())
