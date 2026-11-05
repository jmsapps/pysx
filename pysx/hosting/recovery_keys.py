"""Private, constant-size host recovery keys, provisioned only on host startup."""

import hashlib
import os
import secrets
import stat
import sys
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pysx.runtime.capsule import KeyRing, RootKey

if TYPE_CHECKING:
    from collections.abc import Callable

_MAGIC = b"PSK1"


def encode_ring(ring: KeyRing) -> bytes:
    raw = _MAGIC + ring.issuing.identifier + ring.issuing.secret

    if ring.retiring is not None:
        assert ring.retiring.retire_at is not None
        raw += ring.retiring.identifier + ring.retiring.secret + ring.retiring.retire_at.to_bytes(8)

    return raw


def decode_ring(raw: bytes) -> KeyRing:
    if len(raw) not in (44, 92) or raw[:4] != _MAGIC:
        raise ValueError("invalid recovery key file")
    retiring = (
        RootKey(raw[44:52], raw[52:84], int.from_bytes(raw[84:92])) if len(raw) == 92 else None
    )

    return KeyRing(RootKey(raw[4:12], raw[12:44]), retiring)


def default_directory(namespace: str) -> Path:
    if not namespace:
        raise ValueError("recovery namespace required")

    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "pysx"
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        base = Path(local) if Path(local).is_absolute() else Path.home() / "AppData" / "Local"
        root = base / "pysx"
    else:
        xdg = os.environ.get("XDG_STATE_HOME", "")
        root = (Path(xdg) if Path(xdg).is_absolute() else Path.home() / ".local" / "state") / "pysx"

    return root / hashlib.sha256(namespace.encode("utf-8")).hexdigest()


def posix_key_storage() -> bool:
    """Descriptor traversal and file ownership are what make the ring safe to read."""

    return (
        hasattr(os, "getuid")
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and {os.open, os.mkdir, os.link, os.unlink} <= os.supports_dir_fd
    )


def _directory(path: Path, *, create: bool) -> int:
    """Traverse by directory descriptor: no symlink following or path races."""

    if sys.platform == "win32":
        raise RuntimeError("posix recovery key storage is unavailable on this platform")

    if not path.is_absolute():
        raise ValueError("recovery key directory must be absolute")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)

    try:
        for part in path.parts[1:]:
            if part in (".", ".."):
                raise ValueError("invalid recovery key directory")

            if create:
                with suppress(FileExistsError):
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)

        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("recovery key directory must be private and owned")

        return descriptor
    except BaseException:
        os.close(descriptor)

        raise


def _read(directory: int, name: str) -> KeyRing:
    if sys.platform == "win32":
        raise RuntimeError("posix recovery key storage is unavailable on this platform")

    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)

    try:
        info = os.fstat(descriptor)

        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
            or info.st_size not in (44, 92)
        ):
            raise ValueError("recovery key file must be private and owned")

        return decode_ring(os.read(descriptor, 93))
    finally:
        os.close(descriptor)


def windows_key_storage() -> bool:
    """DPAPI replaces the ownership and mode checks POSIX gets from the filesystem."""

    return sys.platform == "win32" and hasattr(os.stat_result, "st_file_attributes")


def _dpapi(raw: bytes, *, protect: bool) -> bytes:
    """Bind the ring to the current user account.

    POSIX refuses a ring whose file is not owned by this uid and mode 0600. Windows
    has no cheap equivalent check, so the ring is sealed to the user's logon secret
    instead: another account cannot unseal it even with the bytes, and DPAPI's own
    integrity check rejects a tampered blob.
    """

    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char)))

    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    # Not use_last_error: ctypes would then save and restore the thread error around
    # the call, and WinError() below would report the restored value, not DPAPI's.
    library = cast("object", ctypes.WinDLL("crypt32"))  # type: ignore[attr-defined, unused-ignore]
    entry = cast(
        "Callable[..., int]",
        getattr(library, "CryptProtectData" if protect else "CryptUnprotectData"),
    )
    # Parameter two is an input label when sealing and an out-pointer for that label
    # when unsealing, so only the sealing call may pass a string.
    label = ctypes.c_wchar_p("pysx recovery keys") if protect else None
    # CRYPTPROTECT_UI_FORBIDDEN: a host must never block startup on a desktop prompt.
    ok = entry(
        ctypes.byref(source),
        label,
        None,
        None,
        None,
        0x1,
        ctypes.byref(result),
    )

    if not ok:
        reason = cast("Callable[[], BaseException]", ctypes.WinError)()  # type: ignore[attr-defined, unused-ignore]
        action = "sealed to" if protect else "unsealed for"

        raise ValueError(
            f"recovery key ring could not be {action} this user account: {reason}"
        )

    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)  # type: ignore[attr-defined, unused-ignore]


def _windows_reparse(path: Path) -> bool:
    if sys.platform != "win32":
        return False

    try:
        return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except FileNotFoundError:
        return False


def _windows_load(directory: Path, *, provision: bool) -> KeyRing:
    """Provision under the per-user profile, sealed with DPAPI.

    `%LOCALAPPDATA%` is already restricted to the account by Windows, so the
    directory supplies isolation and DPAPI supplies confidentiality. Reparse points
    are rejected along the way because they are the Windows shape of the symlink
    race that `O_NOFOLLOW` closes on POSIX.
    """

    if not windows_key_storage():
        raise RuntimeError(
            "recovery keys require DPAPI on this platform; "
            "host it with HostConfig(recovery=False)"
        )

    if not directory.is_absolute():
        raise ValueError("recovery key directory must be absolute")

    for parent in (*reversed(directory.parents), directory):
        if _windows_reparse(parent):
            raise ValueError("recovery key directory must not traverse a reparse point")

    if provision:
        directory.mkdir(parents=True, exist_ok=True)
    key = directory / "recovery.keys"

    if _windows_reparse(key):
        raise ValueError("recovery key file must not be a reparse point")

    if key.exists():
        sealed = key.read_bytes()

        if not key.is_file() or not 0 < len(sealed) <= 4096:
            raise ValueError("invalid recovery key file")

        return decode_ring(_dpapi(sealed, protect=False))

    if not provision:
        raise FileNotFoundError(key)
    raw = encode_ring(KeyRing(RootKey(secrets.token_bytes(8), secrets.token_bytes(32))))
    temporary = directory / (".key-" + secrets.token_hex(16))

    try:
        with temporary.open("wb") as stream:
            stream.write(_dpapi(raw, protect=True))
            stream.flush()
            os.fsync(stream.fileno())
        # os.replace is atomic on NTFS, so a concurrent host either sees the old
        # ring or this one, never a partial file.
        with suppress(FileExistsError):
            temporary.replace(key)
    finally:
        temporary.unlink(missing_ok=True)

    return decode_ring(_dpapi(key.read_bytes(), protect=False))


def load_keys(directory: Path, *, provision: bool = False) -> KeyRing:
    """Read a platform mount, or atomically provision the local single-host ring."""

    if sys.platform == "win32":
        return _windows_load(directory, provision=provision)

    if not posix_key_storage():
        raise RuntimeError(
            "recovery keys require POSIX directory descriptors and file ownership; "
            "host this platform with HostConfig(recovery=False)"
        )
    parent = _directory(directory, create=provision)
    temporary = ".key-" + secrets.token_hex(16)

    try:
        try:
            return _read(parent, "recovery.keys")
        except FileNotFoundError:
            if not provision:
                raise
        raw = encode_ring(KeyRing(RootKey(secrets.token_bytes(8), secrets.token_bytes(32))))
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )

        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())

            with suppress(FileExistsError):
                os.link(temporary, "recovery.keys", src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            os.unlink(temporary, dir_fd=parent)
        os.fsync(parent)

        return _read(parent, "recovery.keys")
    finally:
        os.close(parent)
