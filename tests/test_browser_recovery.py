"""Production checkpoint protection, host keys and trusted login-session binding."""

import asyncio
import base64
import importlib.util
import json
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

import browser_capture_fixture as capture_fixture
import pytest
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV

from pysx import AccessDenied, Application, AuthenticatedIdentity, HostConfig, native
from pysx.components.checkpoints import MAX_BYTES, MAX_NODES, Encoder, Limit, Unsupported, sites
from pysx.hosting import recovery_keys
from pysx.hosting.asgi import ASGI
from pysx.hosting.hosting import request_from_headers
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
from pysx.runtime.session import Session

if TYPE_CHECKING:
    from collections.abc import Callable

    from pysx import Fragment
    from pysx.hosting.asgi import Event


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
