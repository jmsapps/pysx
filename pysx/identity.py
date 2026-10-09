"""Canonical owner paths and browser-safe comment ranges."""

from dataclasses import dataclass


def key_segment(key: str) -> str:
    """Keep ordinary keys readable; encode every other key without collisions."""

    if key and key.isascii() and all(char.isalnum() or char == "_" for char in key):
        return key

    return "~" + key.encode("utf-8").hex()


@dataclass(frozen=True)
class OwnerPath:
    value: str

    def row(self, key: str) -> OwnerPath:
        return OwnerPath(f"{self.value}:{key_segment(key)}:")

    def wrap(self, markup: str, kind: str) -> str:
        token = self.value.encode("utf-8").hex()

        return f"<!--pysx:{kind}:{token}:start-->{markup}<!--pysx:{kind}:{token}:end-->"
