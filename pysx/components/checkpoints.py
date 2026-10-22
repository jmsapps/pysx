"""Bounded ordinary local-state capture, independent of development handoffs."""

from __future__ import annotations

import ast
import io
import json
import math
import tokenize
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
from inspect import currentframe
from pathlib import Path
from typing import TYPE_CHECKING, cast
from weakref import ref

from pysx.reactive import reactive

if TYPE_CHECKING:
    from collections.abc import Generator
    from types import CodeType, FrameType
    from weakref import ReferenceType

    from pysx.reactive.reactive import Signal

MAX_BYTES = 24 * 1024 - 24  # Capsule time/revision metadata occupies 24 bytes.
MAX_NODES = 4096
MAX_DEPTH = 16
MAX_SLOTS = 512
MAX_METADATA = 128 * 1024
MAX_SOURCE = 64 * 1024


class Limit(ValueError):  # noqa: N818 - internal bounded-encoder outcome
    pass


class Unsupported(ValueError):  # noqa: N818 - internal bounded-encoder outcome
    pass


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


def value_kind(value: object) -> str:
    kind = type(value)

    for supported in (type(None), bool, int, float, str, list, dict):
        if kind is supported:
            return supported.__name__

    return "unsupported"


class Encoder:
    """Visit before encoding; string scratch is at most 384 ASCII bytes per chunk."""

    def __init__(self) -> None:
        self.parts: list[bytes] = []
        self.size = 0
        self.nodes = 0
        self.seen: set[int] = set()

    def write(self, value: bytes) -> None:
        if self.size + len(value) > MAX_BYTES:
            raise Limit("snapshot byte limit")
        self.parts.append(value)
        self.size += len(value)

    def visit(self, depth: int) -> None:
        self.nodes += 1

        if self.nodes > MAX_NODES:
            raise Limit("snapshot node limit")

        if depth > MAX_DEPTH:
            raise Unsupported("value nesting limit")

    def value(self, value: object, depth: int = 0) -> None:
        self.visit(depth)
        kind = type(value)

        if kind is str:
            string = cast("str", value)

            if len(string) + 2 > MAX_BYTES - self.size:
                raise Limit("snapshot byte limit")
            self.write(b'"')

            for offset in range(0, len(string), 32):
                chunk = json.dumps(string[offset : offset + 32], ensure_ascii=True)[1:-1]
                self.write(chunk.encode("ascii"))
            self.write(b'"')
        elif value is None or kind is bool:
            self.write(b"null" if value is None else b"true" if value else b"false")
        elif kind is int:
            if not -(2**63) <= cast("int", value) < 2**63:
                raise Unsupported("integer range")
            self.write(str(value).encode("ascii"))
        elif kind is float:
            if not math.isfinite(cast("float", value)):
                raise Unsupported("nonfinite float")
            self.write(json.dumps(value, allow_nan=False).encode("ascii"))
        elif kind is list or kind is dict:
            if id(value) in self.seen:
                raise Unsupported("aliased or cyclic value")
            self.seen.add(id(value))
            self.write(b"[" if kind is list else b"{")

            if kind is list:
                for index, child in enumerate(cast("list[object]", value)):
                    if index:
                        self.write(b",")
                    self.value(child, depth + 1)
            else:
                for index, (key, child) in enumerate(cast("dict[object, object]", value).items()):
                    if type(key) is not str:
                        raise Unsupported("non-string map key")

                    if index:
                        self.write(b",")
                    self.value(key, depth + 1)
                    self.write(b":")
                    self.value(child, depth + 1)
            self.write(b"]" if kind is list else b"}")
        else:
            raise Unsupported("unsupported value type")

    def finish(self) -> bytes:
        return b"".join(self.parts)


@dataclass(frozen=True, slots=True)
class Site:
    identity: str
    initializer: str
    ambiguous: bool


@lru_cache(maxsize=128)
def sites(
    reference: ReferenceType[CodeType], module: str, package: str
) -> dict[tuple[int, int, int, int], Site]:
    """Bounded metadata; weak keys retain neither user code constants nor source/AST."""
    from pysx.compiler.scope import ScopeIndex

    code = reference()

    if code is None:
        return {}

    if code.co_filename.startswith("<") or len(code.co_code) > MAX_SOURCE:
        return {}

    try:
        with Path(code.co_filename).open("rb") as stream:
            source = stream.read(MAX_SOURCE + 1)

        if len(source) > MAX_SOURCE:
            return {}

        for count, _token in enumerate(tokenize.tokenize(io.BytesIO(source).readline)):
            if count > 8192:
                return {}
        tree = ast.parse(source, filename=code.co_filename)
        index = ScopeIndex(tree, package=package)
    except OSError, ValueError, SyntaxError, tokenize.TokenError, UnicodeError:
        return {}
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    result: dict[tuple[int, int, int, int], Site] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        scope = index.nodes.get(node)
        identity = scope.identity(node.func) if scope is not None else None

        if identity not in (
            "pysx.signal",
            "pysx.reactive.reactive.signal",
            "pysx.local_state",
            "pysx.components.lifecycle.local_state",
        ):
            continue
        parent = parents.get(node)
        binding = (
            ast.dump(parent.targets[0])
            if isinstance(parent, ast.Assign)
            else (ast.dump(parent.target) if isinstance(parent, ast.AnnAssign) else "anonymous")
        )
        ambiguous = False
        ancestor = parent

        while ancestor is not None:
            if isinstance(
                ancestor,
                (
                    ast.For,
                    ast.AsyncFor,
                    ast.While,
                    ast.comprehension,
                    ast.ListComp,
                    ast.DictComp,
                    ast.SetComp,
                    ast.GeneratorExp,
                ),
            ):
                ambiguous = True
            ancestor = parents.get(ancestor)
        canonical = "local_state" if identity.endswith("local_state") else "signal"
        signature = ast.dump(
            ast.Call(func=ast.Name(id=canonical), args=node.args, keywords=node.keywords)
        )

        if node.end_lineno is not None and node.end_col_offset is not None:
            result[node.lineno, node.end_lineno, node.col_offset, node.end_col_offset] = Site(
                digest(f"{module}:{code.co_qualname}:{canonical}:{binding}"),
                digest(signature),
                ambiguous,
            )

    return result


@dataclass(slots=True)
class Slot:
    owner: str
    signal: Signal[object]
    site: str
    mark: str
    kind: str
    reason: str
    cost: int


@dataclass(frozen=True)
class Captured:
    plaintext: bytes | None
    reason: str
    slots: int
    nodes: int
    resets: int


@dataclass
class Construction:
    registry: Checkpoints
    path: str
    component: str
    seen: set[str] = field(default_factory=set[str])
    counts: dict[str, int] = field(default_factory=dict[str, int])


_construction: ContextVar[Construction | None] = ContextVar(
    "pysx_checkpoint_construction", default=None
)


class Checkpoints:
    def __init__(self) -> None:
        self.slots: dict[str, Slot] = {}
        self.metadata_bytes = 0
        self.revision = 0
        self.overflow = False
        self.cached: Captured | None = None

    def changed(self) -> None:
        self.revision += 1
        self.cached = None

    def remove(self, key: str) -> None:
        slot = self.slots.pop(key)
        slot.signal.checkpoint_changed = None
        self.metadata_bytes -= slot.cost
        self.changed()

    def release(self, path: str) -> None:
        for key, slot in tuple(self.slots.items()):
            if slot.owner == path:
                self.remove(key)

    @contextmanager
    def enter(self, path: str, component: str) -> Generator[None]:
        construction = Construction(self, path, component)
        token = _construction.set(construction)

        try:
            yield

            for key, slot in tuple(self.slots.items()):
                if slot.owner == path and key not in construction.seen:
                    self.remove(key)
                elif slot.owner == path and construction.counts.get(slot.site, 0) > 1:
                    slot.reason = "ambiguous call site"
                    self.changed()
        finally:
            _construction.reset(token)

    def capture(self) -> Captured:
        if self.cached is not None:
            return self.cached
        encoder = Encoder()
        resets = 0

        try:
            if self.overflow:
                raise Limit("capture metadata limit")
            encoder.visit(0)
            encoder.write(b"[")

            for index, (key, slot) in enumerate(self.slots.items()):
                if index:
                    encoder.write(b",")
                encoder.write(b"[")

                encoder.visit(1)

                for metadata in (key, slot.mark, slot.kind):
                    encoder.value(metadata, 2)
                    encoder.write(b",")
                # Unsupported values reset individually; a total-budget overflow clears all.
                start, size = len(encoder.parts), encoder.size
                visited = encoder.nodes
                reason = slot.reason
                encoder.seen.clear()

                try:
                    if reason:
                        raise Unsupported(reason)
                    value = slot.signal.checkpoint_value()

                    if value_kind(value) != slot.kind:
                        raise Unsupported("value type changed")
                    encoder.value(value, 2)

                    if not slot.mark:
                        raise Unsupported("initializer unavailable")
                except Unsupported as error:
                    del encoder.parts[start:]
                    encoder.size = size

                    if encoder.nodes == visited:
                        encoder.visit(2)
                    encoder.write(b"null")
                    reason = str(error)
                    resets += 1
                encoder.write(b",")
                encoder.value(reason, 2)
                encoder.write(b"]")
            encoder.write(b"]")
            result = Captured(encoder.finish(), "", len(self.slots), encoder.nodes, resets)
        except Limit as error:
            result = Captured(None, str(error), len(self.slots), encoder.nodes, resets)
        self.cached = result

        return result

    def close(self) -> None:
        for key in tuple(self.slots):
            self.remove(key)
        self.cached = None


def _record(
    created: Signal[object], caller: FrameType | None, name: str | None, initial: object
) -> None:
    construction = _construction.get()

    if construction is None or caller is None:
        return
    registry = construction.registry

    if (
        len(construction.path) > 1024
        or len(construction.component) > 512
        or (name is not None and len(name) > 256)
    ):
        registry.overflow = True
        registry.changed()

        return
    module = caller.f_globals.get("__name__", "")
    package = caller.f_globals.get("__package__", "")
    module = module if isinstance(module, str) else ""
    package = package if isinstance(package, str) else ""
    position = next(
        (
            position
            for offset, position in enumerate(caller.f_code.co_positions())
            if offset == caller.f_lasti // 2
        ),
        None,
    )

    if len(module) > 512 or len(package) > 512:
        module, package = "", ""
    site = sites(ref(caller.f_code), module, package).get(
        cast("tuple[int, int, int, int]", position)
    )
    source = (
        site.identity
        if site is not None
        else digest(f"{module}:{caller.f_code.co_qualname}:unknown")
    )

    if name is not None:
        source = digest("named:" + name)
    occurrence = 0 if name is not None else construction.counts.get(source, 0)

    if source not in construction.counts and len(construction.counts) >= MAX_SLOTS:
        registry.overflow = True
        registry.changed()

        return
    construction.counts[source] = 1 if name is not None else occurrence + 1
    key = digest(construction.path + ":" + construction.component) + ":" + source + f":{occurrence}"
    cost = 256 + len(key) + len(construction.path.encode("utf-8"))

    if key not in registry.slots and (
        len(registry.slots) >= MAX_SLOTS or registry.metadata_bytes + cost > MAX_METADATA
    ):
        registry.overflow = True
        registry.changed()

        return
    construction.seen.add(key)
    existing = registry.slots.get(key)

    if existing is not None and existing.signal is created:
        return

    if existing is not None:
        registry.remove(key)
    reason = (
        "source unavailable" if site is None else "ambiguous call site" if site.ambiguous else ""
    )
    initial_encoder = Encoder()

    try:
        initial_encoder.value(initial)
        initial_mark = sha256(initial_encoder.finish()).hexdigest()[:16]
    except Limit:
        initial_mark = ""
    except Unsupported as error:
        initial_mark = ""
        reason = str(error)
    mark = (
        digest((site.initializer if site is not None else "") + initial_mark)
        if initial_mark
        else ""
    )
    registry.slots[key] = Slot(
        construction.path, created, source, mark, value_kind(initial), reason, cost
    )
    registry.metadata_bytes += cost
    created.checkpoint_changed = registry.changed
    registry.changed()


def record_signal(created: Signal[object]) -> None:
    if _construction.get() is None:
        return
    frame = currentframe()
    caller = None

    try:
        caller = frame.f_back.f_back if frame is not None and frame.f_back is not None else None
        _record(created, caller, None, created.checkpoint_value())
    finally:
        del frame
        del caller


def record_named(created: Signal[object], name: str, initial: object) -> None:
    frame = currentframe()
    caller = None

    try:
        caller = frame.f_back.f_back if frame is not None and frame.f_back is not None else None
        _record(created, caller, name, initial)
    finally:
        del frame
        del caller


reactive.checkpoint_created = record_signal
