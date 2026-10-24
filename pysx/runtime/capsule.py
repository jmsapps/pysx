"""Bounded, context-bound browser checkpoints; no per-browser server registry."""

import base64
import binascii
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from threading import Lock

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAX_PLAINTEXT = 24 * 1024
MAX_CAPSULE = 36 * 1024
LIFETIME = 24 * 60 * 60
SKEW = 60
DOMAIN = b"pysx.browser-capsule.v1"
HEADER_SIZE = 1 + 8 + 32 + 12


class CapsuleError(ValueError):
    """Invalid checkpoint, with no token or identity included in the exception."""


@dataclass(frozen=True, repr=False)
class RootKey:
    identifier: bytes
    secret: bytes
    retire_at: int | None = None

    def __post_init__(self) -> None:
        if len(self.identifier) != 8 or len(self.secret) != 32:
            raise ValueError("invalid recovery root key")

        if self.retire_at is not None and (
            type(self.retire_at) is not int or not 0 <= self.retire_at < 2**64
        ):
            raise ValueError("invalid recovery retirement time")


@dataclass(frozen=True, repr=False)
class KeyRing:
    issuing: RootKey
    retiring: RootKey | None = None

    def __post_init__(self) -> None:
        if self.issuing.retire_at is not None:
            raise ValueError("issuing recovery key cannot be retired")

        if self.retiring is not None and (
            self.retiring.identifier == self.issuing.identifier or self.retiring.retire_at is None
        ):
            raise ValueError("invalid retiring recovery key")

    def lookup(self, identifier: bytes, now: int) -> RootKey:
        for key in (self.issuing, self.retiring):
            if key is not None and key.identifier == identifier:
                if key.retire_at is None or now <= key.retire_at + LIFETIME + SKEW:
                    return key

                break

        raise CapsuleError("recovery key unavailable")


@dataclass(frozen=True, repr=False)
class CapsuleContext:
    namespace: str
    origin: str
    base_path: str
    browser: str
    principal: str | None
    authentication_session: str | None
    document: str
    route: str

    def encoded(self) -> bytes:
        if self.principal is not None and not self.authentication_session:
            raise CapsuleError("authentication session unavailable")
        values = (
            self.namespace,
            self.origin,
            self.base_path,
            self.browser,
            self.principal,
            self.authentication_session,
            self.document,
            self.route,
        )

        if any(value is not None and len(value) > 4096 for value in values):
            raise CapsuleError("recovery context too large")

        if not self.namespace or not self.origin or not self.browser or not self.document:
            raise CapsuleError("recovery context unavailable")

        return json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _derive(root: RootKey, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=DOMAIN).derive(root.secret)


def _aad(header: bytes, context: bytes) -> bytes:
    return hashlib.sha256(DOMAIN + header + context).digest()


@dataclass(frozen=True, repr=False)
class OpenedCapsule:
    state: bytes
    revision: int
    issued_at: int


class CapsuleIssuer:
    def __init__(self, ring: KeyRing) -> None:
        self.ring = ring
        self._salt = secrets.token_bytes(32)
        self._key = _derive(ring.issuing, self._salt)
        # Probe at startup; UnsupportedAlgorithm propagates, never falls back.
        AESGCMSIV(self._key)
        self._count = 0
        self._lock = Lock()

    def seal(
        self, state: bytes, context: CapsuleContext, *, revision: int, now: int | None = None
    ) -> str:
        if type(revision) is not int or not 0 <= revision < 2**63:
            raise CapsuleError("invalid recovery revision")

        if len(state) > MAX_PLAINTEXT - 24:
            raise CapsuleError("recovery snapshot too large")
        encoded_context = context.encoded()
        issued = int(time.time()) if now is None else now
        plaintext = (
            issued.to_bytes(8) + (issued + LIFETIME).to_bytes(8) + revision.to_bytes(8) + state
        )
        with self._lock:
            if self._count >= 2**32:
                self._salt = secrets.token_bytes(32)
                self._key = _derive(self.ring.issuing, self._salt)
                self._count = 0
            salt, key = self._salt, self._key
            self._count += 1
        nonce = secrets.token_bytes(12)
        header = b"\x01" + self.ring.issuing.identifier + salt + nonce
        ciphertext = AESGCMSIV(key).encrypt(nonce, plaintext, _aad(header, encoded_context))

        return base64.urlsafe_b64encode(header + ciphertext).decode("ascii")

    def open(self, token: str, context: CapsuleContext, *, now: int | None = None) -> OpenedCapsule:
        if not HEADER_SIZE + 40 <= len(token) <= MAX_CAPSULE or not token.isascii():
            raise CapsuleError("invalid recovery envelope")

        try:
            raw = base64.b64decode(token, altchars=b"-_", validate=True)
        except ValueError, binascii.Error:
            raise CapsuleError("invalid recovery envelope") from None

        if (
            len(raw) < HEADER_SIZE + 40
            or len(raw) > HEADER_SIZE + MAX_PLAINTEXT + 16
            or raw[0] != 1
            or base64.urlsafe_b64encode(raw).decode("ascii") != token
        ):
            raise CapsuleError("invalid recovery envelope")
        current = int(time.time()) if now is None else now
        root = self.ring.lookup(raw[1:9], current)
        header = raw[:HEADER_SIZE]

        try:
            plaintext = AESGCMSIV(_derive(root, raw[9:41])).decrypt(
                raw[41:53], raw[HEADER_SIZE:], _aad(header, context.encoded())
            )
        except InvalidTag:
            raise CapsuleError("invalid recovery capsule") from None
        issued = int.from_bytes(plaintext[:8])
        expires = int.from_bytes(plaintext[8:16])
        revision = int.from_bytes(plaintext[16:24])

        if expires != issued + LIFETIME or issued > current + SKEW or current > expires + SKEW:
            raise CapsuleError("recovery capsule expired")

        if revision >= 2**63:
            raise CapsuleError("invalid recovery revision")

        return OpenedCapsule(plaintext[24:], revision, issued)
