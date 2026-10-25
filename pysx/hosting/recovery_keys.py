"""Private, constant-size host recovery keys, provisioned only on host startup."""

import hashlib
import os
import secrets
import stat
import sys
from contextlib import suppress
from pathlib import Path

from pysx.runtime.capsule import KeyRing, RootKey

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


def load_keys(directory: Path, *, provision: bool = False) -> KeyRing:
    """Read a platform mount, or atomically provision the local single-host ring."""

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
