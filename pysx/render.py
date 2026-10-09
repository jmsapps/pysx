"""Node tree -> HTML plus a set of watchers that produce wire ops.

Each reactive hole gets a Watcher. A watcher reads its signal (which registers
the subscription) and emits an op only when the value actually changed, so an
event that touches nothing sends no frame.

Keyed rows own independent builders and child watchers. Retained snapshots follow
live patches, allowing value changes to preserve compatible descendant nodes.
"""

from __future__ import annotations

import html as _htmlmod
import json
import re
from annotationlib import Format
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from inspect import Parameter, signature
from itertools import islice
from string.templatelib import Interpolation, Template
from typing import TYPE_CHECKING, cast

from ._compiler_runtime import MissingComponent
from .bindings import MAX_ROWS, Deferred, Environment, resolve
from .bindings import Binding as LexicalBinding
from .composition import Children, Component, identity_for, namespace_for
from .dom import DomController, DomError, DomRef, dom_context
from .elements import VOID, ElementTag
from .events import EventHandler
from .forms import Binding, make_binding
from .identity import OwnerPath
from .lifecycle import Scopes, active_scope
from .parser import (
    Case,
    Conditional,
    Element,
    Hole,
    HoleKind,
    InterpolationError,
    Local,
    Loop,
    Match,
    Node,
    Position,
    Skeleton,
    attr_kind,
    parse,
)
from .reactive import Effect, Readable, Signal, derived, untracked
from .render_tree import apply, diff, snapshot
from .schema import BOOLEAN_ATTRS, NATIVE_TAGS, normalize_attr
from .styled import StyledCallable, StyledTag, VariantClass, flatten, global_rules
from .styles import (
    CssClass,
    StyleRegistry,
    Themes,
    css_text,
    style_context,
    style_owner,
    variable_values,
)
from .template import template_values

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .wire import Op


@dataclass(frozen=True)
class Fragment:
    template: Template
    namespace: Mapping[str, object] | None = None
    root_classes: tuple[object, ...] = ()
    themes: Themes | None = None
    rules: tuple[tuple[str, str], ...] = ()
    bound: bool = False

    def scope_namespace(self, fallback: Mapping[str, object]) -> dict[str, object]:
        bindings = dict(self.namespace or {})

        return bindings if self.bound else dict(fallback) | bindings


type Used = Callable[..., object] | ElementTag | StyledTag


def _lexical_live(value: object) -> bool:
    return isinstance(value, (Signal, Deferred, LexicalBinding))


def _signal_reader(value: object) -> Readable[object] | None:
    return cast("Readable[object]", value) if isinstance(value, Signal) else None


def _structured_content(value: object) -> bool:
    return isinstance(value, (Template, Fragment, list, tuple))


def _validate_snapshot(
    value: object,
    depth: int = 0,
    path: frozenset[int] = frozenset(),
    budget: list[int] | None = None,
) -> None:
    if not _structured_content(value):
        return

    if depth >= 128 or id(value) in path:
        raise ValueError("snapshot nesting exceeds 128 levels or contains a cycle")
    nested_path = path | {id(value)}
    counter = budget if budget is not None else [0]

    if isinstance(value, Fragment):
        _validate_snapshot(value.template, depth + 1, nested_path, counter)
    elif isinstance(value, Template):
        for entry in value.values:
            _validate_snapshot(entry, depth + 1, nested_path, counter)
    else:
        for entry in cast("Sequence[object]", value):
            counter[0] += 1

            if counter[0] > MAX_ROWS:
                raise ValueError("snapshot exceeds 10000 entries")
            _validate_snapshot(entry, depth + 1, nested_path, counter)


def _used(components: tuple[Used, ...]) -> dict[str, object]:
    found: dict[str, object] = {}

    for component in components:
        if not callable(component) and not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            component, (ElementTag, StyledTag)
        ):
            raise TypeError(f"use= takes components, received {type(component).__name__}")
        name = getattr(component, "__name__", None)

        if isinstance(name, str):
            found[name] = component

    return found


def pysx(
    template: Template,
    *,
    use: tuple[Used, ...] = (),
    namespace: Mapping[str, object] | None = None,
    themes: Themes | None = None,
) -> Fragment:
    if not isinstance(template, Template):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError('pysx() takes a t-string; did you write f""" instead of t"""?')
    bindings = _used(use)

    if namespace is not None:
        bindings.update(namespace)

    return Fragment(template, bindings or None, themes=themes)


def _template_skeleton(template: Template) -> Skeleton:
    skeleton = parse(template.strings)

    for index, kind, name in skeleton.holes:
        interpolation = template.interpolations[index]

        if interpolation.conversion is not None or interpolation.format_spec:
            position = _hole_position(skeleton.root, index)

            if name is not None and normalize_attr(name) in {"css", "stylevars", "cssvars"}:
                raise InterpolationError(
                    "CSS attribute interpolation metadata is unsupported", position
                )

            if kind not in {HoleKind.TEXT, HoleKind.ATTR}:
                raise InterpolationError(
                    f"{kind.value} interpolation conversion/format metadata is unsupported",
                    position,
                )

    return skeleton


def _hole_position(nodes: Sequence[Node], index: int) -> Position | None:
    for node in nodes:
        holes: list[Hole] = []

        if isinstance(node, Hole):
            holes.append(node)
        elif isinstance(node, Element):
            if isinstance(node.tag, Hole):
                holes.append(node.tag)
            holes.extend(value for _name, value in node.attrs if isinstance(value, Hole))
            found = _hole_position(node.children, index)

            if found is not None:
                return found
        elif isinstance(node, Conditional):
            holes.append(node.hole)
            found = _hole_position((*node.then, *node.otherwise), index)

            if found is not None:
                return found

        if isinstance(node, Loop):
            holes.append(node.source)

            if node.key is not None:
                holes.append(node.key)
            found = _hole_position(node.children, index)

            if found is not None:
                return found
        elif isinstance(node, Local):
            holes.append(node.value)
        elif isinstance(node, Match):
            holes.append(node.value)

            for case in node.cases:
                holes.extend(pattern for pattern in case.patterns if isinstance(pattern, Hole))
                found = _hole_position(case.children, index)

                if found is not None:
                    return found

        for hole in holes:
            if hole.index == index:
                return hole.position

    return None


def _prepared_values(template: Template) -> tuple[object, ...]:
    _template_skeleton(template)

    return template_values(template)


def component[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    setattr(fn, "_pysx_component", True)  # noqa: B010 - dynamic compatibility marker

    return fn


@dataclass(frozen=True)
class Each[T]:
    source: Readable[Iterable[T]]
    item: Callable[[T], Fragment]
    key: Callable[[T], str | int]
    live: bool = True


def _bounded_items[T](source: Iterable[T]) -> tuple[T, ...]:
    items = tuple(islice(source, MAX_ROWS + 1))

    if len(items) > MAX_ROWS:
        raise ValueError("each exceeds 10000 rows")

    return items


def each[T](
    source: Readable[Iterable[T]] | Iterable[T],
    item: Callable[[T], Fragment],
    *,
    key: Callable[[T], str | int],
) -> Each[T]:
    """Build keyed rows; readable sources are live, ordinary iterables are frozen."""

    if callable(source):
        return Each(source, item, key)
    items = _bounded_items(source)

    return Each(lambda: items, item, key, live=False)


def each_indexed[T](
    source: Readable[Iterable[T]] | Iterable[T],
    item: Callable[[int, T], Fragment],
    *,
    key: Callable[[T], str | int],
) -> Each[tuple[int, T]]:
    """Expose current positions while keeping identity attached to item keys."""

    if callable(source):
        def indexed() -> Iterable[tuple[int, T]]:
            return enumerate(source())

        return each(indexed, lambda row: item(*row), key=lambda row: key(row[1]))

    return each(enumerate(source), lambda row: item(*row), key=lambda row: key(row[1]))


@dataclass(frozen=True)
class _Branch:
    index: int
    builder: Callable[[], Fragment]


def when(
    *,
    conditions: Iterable[tuple[bool | Readable[bool], Callable[[], Fragment]]],
    default: Callable[[], Fragment] | None = None,
) -> Each[_Branch]:
    """Construct only the first matching branch, with live reads and owned cleanup."""
    choices = _bounded_items(conditions)

    def selected() -> Iterable[_Branch]:
        for index, (condition, builder) in enumerate(choices):
            value = cast("object", condition() if callable(condition) else condition)

            if not isinstance(value, bool):
                raise TypeError("when conditions must be bool or readable bool")

            if value:
                return (_Branch(index, builder),)

        return () if default is None else (_Branch(len(choices), default),)

    return each(selected, lambda branch: branch.builder(), key=lambda branch: branch.index)


def _read(value: object) -> object:
    return cast("Readable[object]", value)() if isinstance(value, Signal) else value


def _seed[T](reader: Callable[[], T]) -> T:
    with untracked():
        return reader()


def _seed_read(value: object) -> object:
    with untracked():
        return _read(value)


def _seed_attr(value: object, name: str = "") -> str | None:
    with untracked():
        return _attr_text(value, name)


def _seed_class(value: object) -> str:
    with untracked():
        return _class_text(value)


def _attr_text(value: object, name: str = "") -> str | None:
    """Native boolean presence differs from ARIA and enumerated string values."""
    value = _read(value)

    if value is None:
        return None

    if name in BOOLEAN_ATTRS:
        return None if value is False or value == 0 or value == "false" else ""

    if isinstance(value, bool):
        return str(value).lower()

    return str(value)


def _class_text(value: object) -> str:
    if isinstance(value, (CssClass, VariantClass)):
        return value()
    value = _read(value)

    if value is None or value is False:
        return ""

    if isinstance(value, dict):
        return " ".join(
            str(key) for key, enabled in cast("dict[object, object]", value).items() if enabled
        )

    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in cast("Iterable[object]", value))

    return str(value)


def _merge_classes(values: Iterable[str]) -> str:
    return " ".join(dict.fromkeys(" ".join(values).split()))


# --------------------------------------------------------------------------- watchers


class Watcher:
    def refresh(self) -> list[Op]:
        raise NotImplementedError

    def dispose(self, preserved: set[int] | None = None) -> None:
        pass


@dataclass
class TextWatcher(Watcher):
    slot: str
    signal: Readable[object]
    last: str

    def refresh(self) -> list[Op]:
        now = str(self.signal())

        if now == self.last:
            return []
        self.last = now

        return [{"op": "text", "id": self.slot, "v": now}]


@dataclass
class CondWatcher(Watcher):
    slot: str
    signal: Readable[object]
    render_branch: Callable[[bool], tuple[str, list[Watcher]]]
    last: bool
    children: list[Watcher]

    def refresh(self) -> list[Op]:
        now = bool(self.signal())

        if now == self.last:
            return [op for watcher in self.children for op in watcher.refresh()]
        self.last = now

        for watcher in self.children:
            watcher.dispose()
        markup, self.children = self.render_branch(now)
        corrections = [op for watcher in self.children for op in watcher.refresh()]

        return [{"op": "html", "id": self.slot, "v": markup}, *corrections]

    def dispose(self, preserved: set[int] | None = None) -> None:
        for watcher in self.children:
            watcher.dispose(preserved)


@dataclass
class ContentWatcher(Watcher):
    slot: str
    render_content: Callable[[], str]
    last: str

    def refresh(self) -> list[Op]:
        markup = self.render_content()

        if markup == self.last:
            return []
        self.last = markup

        return [{"op": "html", "id": self.slot, "v": markup}]


@dataclass
class AttrWatcher(Watcher):
    element: str
    name: str
    signal: Readable[object]
    last: str | None

    def refresh(self) -> list[Op]:
        now = _attr_text(self.signal, self.name)

        if now == self.last:
            return []
        self.last = now

        return [{"op": "attr", "id": self.element, "name": self.name, "v": now}]


@dataclass
class BindingWatcher(Watcher):
    binding: Binding
    last: str | bool | list[str]

    def refresh(self) -> list[Op]:
        now = self.binding.value()

        if now == self.last:
            return []
        self.last = now

        return [self.binding.op()]


@dataclass
class ClassWatcher(Watcher):
    """`class` is merged from several sources — the styled hash, any literal
    class, and the hole. Patching only the hole's value would replace the whole
    attribute and strip the scoped class, killing the element's styling."""

    element: str
    sources: tuple[object, ...]
    last: str

    def merged(self) -> str:
        return _merge_classes(_class_text(value) for value in self.sources)

    def refresh(self) -> list[Op]:
        now = self.merged()

        if now == self.last:
            return []
        self.last = now

        return [{"op": "attr", "id": self.element, "name": "class", "v": now}]


@dataclass
class CssWatcher(Watcher):
    registry: StyleRegistry
    owner: str
    source: object

    def refresh(self) -> list[Op]:
        self.registry.replace(self.owner, css_text(self.source))

        return []


@dataclass
class InlineStyleWatcher(Watcher):
    element: str
    source: object
    variables: object
    last: str = ""

    def value(self) -> str:
        base = _attr_text(self.source) or ""
        variables = variable_values(self.variables)
        custom = ";".join(f"{name}:{value}" for name, value in sorted(variables.items()))

        return ";".join(part.strip().rstrip(";") for part in (base, custom) if part)

    def refresh(self) -> list[Op]:
        value = self.value()

        if value == self.last:
            return []
        self.last = value

        return [{"op": "attr", "id": self.element, "name": "style", "v": value or None}]


@dataclass
class ThemeWatcher(Watcher):
    registry: StyleRegistry
    themes: Themes

    def refresh(self) -> list[Op]:
        self.registry.theme = self.themes.css()

        return []


class _Row:
    def __init__(self, owner: ListWatcher, key: str, value: object) -> None:
        self.owner, self.key, self.value = owner, key, value
        out = owner.out
        out.generation += 1
        self.prefix = f"{OwnerPath(owner.slot).row(key).value}g{out.generation}:"
        self.scope = OwnerPath(owner.scope_slot).row(key).value
        self.markup = ""
        self.tree = snapshot("")
        self.watchers: list[Watcher] = []
        self.effects: list[Effect] = []
        self.builder = Effect(self._build)

    def _build(self) -> None:
        out = self.owner.out

        if self.markup:
            with untracked():
                if not any(
                    str(self.owner.spec.key(value)) == self.key
                    for value in _bounded_items(self.owner.signal())
                ):
                    return
        out.depth += 1
        dom_token = dom_context.set(out.dom)
        style_token = style_context.set(out.styles)
        out.building.append(self)

        try:
            with out.context():
                for effect in self.effects:
                    effect.dispose()
                self.effects.clear()
                old_watchers = self.watchers
                out.clear_owned(self.prefix)
                start = len(out.watchers)

                try:
                    with out.scopes.reconcile(self.scope):
                        markup = self.owner.build(self.value, self.key, self.prefix, self.scope)
                    self.watchers = out.watchers[start:]
                finally:
                    del out.watchers[start:]
                retained_lists = {
                    bytes.fromhex(token).decode()
                    for token in re.findall(r"<!--pysx:list:([a-f0-9]+):start-->", markup)
                }
                live_lists = {slot for slot in retained_lists if slot in out.lists}
                preserved = {id(out.lists[slot]) for slot in live_lists}
                preserved.update(id(watcher) for watcher in self.watchers)

                for watcher in old_watchers:
                    if id(watcher) not in preserved:
                        watcher.dispose(preserved)
                tokens = set(re.findall(r'data-pysx-ref="([^"]+)"', markup))

                for path, (_signature, ref) in tuple(out.refs.items()):
                    if path.startswith(self.prefix) and ref.token not in tokens:
                        out.dom.revoke(f"{path}:ref:")
                        out.refs.pop(path)
                paths = set(re.findall(r'data-pysx-el="([^"]+)"', markup))

                for path in tuple(out.elements):
                    if path.startswith(self.prefix) and path not in paths:
                        out.elements.pop(path)
                        out.retired.add(path)

                current = snapshot(markup)
                apply(current, out.pending)
                ops = (
                    diff(
                        self.tree,
                        current,
                        OwnerPath(self.owner.slot).row(self.key).value,
                        live_lists,
                    )
                    if self.markup
                    else []
                )
                self.tree = current
                self.markup = current.markup()
                self.owner.markup[self.key] = self.markup
                out.emit(ops)

                for watcher in self.watchers:
                    def collect(watcher: Watcher = watcher) -> None:
                        out.collect(watcher)

                    self.effects.append(Effect(collect))
        finally:
            out.building.remove(self)
            out.depth -= 1
            dom_context.reset(dom_token)
            style_context.reset(style_token)
            out.flush()

    def dispose(self) -> None:
        errors: list[Exception] = []
        actions: list[Callable[[], None]] = [
            self.builder.dispose,
            *(effect.dispose for effect in self.effects),
            *(watcher.dispose for watcher in self.watchers),
            lambda: self.owner.out.release(self.prefix, self.scope),
        ]

        for close in actions:
            try:
                close()
            except Exception as error:
                errors.append(error)
        self.effects.clear()
        self.watchers.clear()

        if errors:
            raise ExceptionGroup("row cleanup failed", errors)


@dataclass
class ListWatcher(Watcher):
    slot: str
    scope_slot: str
    signal: Readable[Iterable[object]]
    spec: Each[object]
    build: Callable[[object, str, str, str], str]
    out: Rendered
    order: list[str] = field(default_factory=list[str])
    markup: dict[str, str] = field(default_factory=dict[str, str])
    rows: dict[str, _Row] = field(default_factory=dict[str, _Row])

    def refresh(self) -> list[Op]:
        incoming: list[tuple[str, object]] = []
        keys: set[str] = set()

        for value in _bounded_items(self.signal()):
            raw_key = self.spec.key(value)

            if type(raw_key) not in (str, int):
                raise TypeError("each keys must be strings or integers, excluding bool")
            key = str(raw_key)

            if key in keys:
                raise ValueError(f"duplicate list key {key!r}")
            keys.add(key)
            incoming.append((key, value))
        self.out.depth += 1
        added: dict[str, str] = {}

        try:
            for key in self.rows.keys() - keys:
                self.rows.pop(key).dispose()
                self.markup.pop(key, None)

            for key, value in incoming:
                row = self.rows.get(key)

                if row is None:
                    row = _Row(self, key, value)
                    self.rows[key] = row
                    added[key] = row.markup
                elif row.value is not value and row.value != value:
                    row.value = value
                    row.builder.run()

                for seen in self.out.scopes.traversals:
                    seen.update(
                        path for path in self.out.scopes.owners if path.startswith(row.scope)
                    )
            order = [key for key, _value in incoming]
            ops: list[Op] = []

            if order != self.order or added:
                ops.append({"op": "list", "id": self.slot, "keys": order, "html": added})
            self.order = order
            ops.extend(self.out.take_pending())

            return ops
        finally:
            self.out.depth -= 1

    def rebind(self, spec: Each[object], build: Callable[[object, str, str, str], str]) -> None:
        changed = self.spec.item is not spec.item
        self.spec, self.signal, self.build = spec, spec.source, build
        self.out.emit(self.refresh())

        if changed:
            for row in self.rows.values():
                row.builder.run()

    def dispose(self, preserved: set[int] | None = None) -> None:
        if preserved and id(self) in preserved:
            return
        errors: list[Exception] = []

        for row in tuple(self.rows.values()):
            try:
                row.dispose()
            except Exception as error:
                errors.append(error)
        self.rows.clear()

        if self.out.lists.get(self.slot) is self:
            self.out.lists.pop(self.slot)

        if errors:
            raise ExceptionGroup("list cleanup failed", errors)


@dataclass
class Rendered:
    scopes: Scopes = field(default_factory=Scopes)
    styles: StyleRegistry = field(default_factory=StyleRegistry)
    dom: DomController = field(default_factory=DomController)
    body: str = ""
    css: str = ""
    generation: int = 0
    depth: int = 0
    pending: list[Op] = field(default_factory=list["Op"])
    lists: dict[str, ListWatcher] = field(default_factory=dict[str, ListWatcher])
    building: list[_Row] = field(default_factory=list[_Row])
    elements: dict[str, tuple[tuple[str, ...], str]] = field(
        default_factory=dict[str, tuple[tuple[str, ...], str]]
    )
    branches: dict[str, tuple[bool, str]] = field(default_factory=dict[str, tuple[bool, str]])
    retired: set[str] = field(default_factory=set[str])
    refs: dict[str, tuple[tuple[str, ...], DomRef]] = field(
        default_factory=dict[str, tuple[tuple[str, ...], DomRef]]
    )
    context: Callable[[], AbstractContextManager[None]] = field(default=lambda: nullcontext())
    sink: Callable[[list[Op]], None] | None = None
    handlers: dict[str, Callable[[object], object]] = field(
        default_factory=dict[str, Callable[[object], object]]
    )
    watchers: list[Watcher] = field(default_factory=list[Watcher])
    # handler id -> element id, so an input's own event can be stopped from
    # echoing a value patch back and resetting the caret.
    bind_elements: dict[str, str] = field(default_factory=dict[str, str])
    bindings: dict[str, Binding] = field(default_factory=dict[str, Binding])
    handler_owners: dict[str, str] = field(default_factory=dict[str, str])
    event_handlers: dict[str, tuple[EventHandler, str]] = field(
        default_factory=dict[str, tuple[EventHandler, str]]
    )

    def emit(self, ops: list[Op]) -> None:
        self.record(ops)
        self.pending.extend(ops)
        self.flush()

    def record(self, ops: list[Op]) -> None:
        rows = {id(row): row for watcher in self.lists.values() for row in watcher.rows.values()}
        rows.update((id(row), row) for row in self.building)

        for row in rows.values():
            owned = [op for op in ops if op["op"] != "css" and op["id"].startswith(row.prefix)]

            if row.markup and owned:
                apply(row.tree, owned)
                row.markup = row.tree.markup()
                row.owner.markup[row.key] = row.markup

    def refresh(self, watcher: Watcher) -> list[Op]:
        prior, self.pending = self.pending, []
        self.depth += 1

        try:
            ops = watcher.refresh()
            ops.extend(self.take_pending())
            self.record(ops)

            return ops
        finally:
            self.pending = prior + self.pending
            self.depth -= 1

    def take_pending(self) -> list[Op]:
        ops, self.pending = self.pending, []

        return ops

    def flush(self) -> None:
        if self.depth == 0 and self.sink is not None and self.pending:
            self.sink(self.take_pending())

    def collect(self, watcher: Watcher) -> None:
        with self.context():
            self.emit(self.refresh(watcher))

    def clear_owned(self, prefix: str) -> None:
        preserved = tuple(
            row.prefix
            for watcher in self.lists.values()
            for row in watcher.rows.values()
            if row.prefix != prefix and row.prefix.startswith(prefix)
        )
        self.clear_handlers(prefix, preserved)
        self.styles.release(prefix, preserved)

    def clear_handlers(self, prefix: str, preserved: tuple[str, ...] = ()) -> None:
        for hid, owner in tuple(self.handler_owners.items()):
            if owner.startswith(prefix) and not owner.startswith(preserved):
                for mapping in (
                    self.handlers,
                    self.bindings,
                    self.bind_elements,
                    self.handler_owners,
                    self.event_handlers,
                ):
                    mapping.pop(hid, None)

    def release(self, prefix: str, scope: str) -> None:
        self.clear_handlers(prefix)
        self.styles.release(prefix)
        self.revoke_refs(prefix)
        self.scopes.release(scope)

        for mapping in (self.elements, self.branches):
            for path in tuple(mapping):
                if path.startswith(prefix):
                    mapping.pop(path)
        self.retired.difference_update(
            path for path in tuple(self.retired) if path.startswith(prefix)
        )

    def revoke_refs(self, prefix: str) -> None:
        self.dom.revoke(prefix)

        for path in tuple(self.refs):
            if path.startswith(prefix):
                self.refs.pop(path)

    def dispose(self) -> None:
        """Release standalone render resources; sessions call this on disconnect."""
        errors: list[Exception] = []

        self.sink = None

        for watcher in tuple(self.watchers):
            try:
                watcher.dispose()
            except Exception as error:
                errors.append(error)

        for watcher in tuple(self.lists.values()):
            try:
                watcher.dispose()
            except Exception as error:
                errors.append(error)
        self.watchers.clear()
        self.lists.clear()
        self.refs.clear()
        self.elements.clear()
        self.branches.clear()
        self.retired.clear()
        self.pending.clear()

        for close in (self.styles.close, self.dom.close, self.scopes.close):
            try:
                close()
            except Exception as error:
                errors.append(error)
        self.handlers.clear()
        self.bindings.clear()
        self.bind_elements.clear()
        self.handler_owners.clear()
        self.event_handlers.clear()

        if errors:
            raise ExceptionGroup("render cleanup failed", errors)


# --------------------------------------------------------------------------- emit


class _Emitter:
    def __init__(
        self,
        ns: dict[str, object],
        values: tuple[object, ...],
        out: Rendered,
        prefix: str = "",
        handler_prefix: str | None = None,
        scope_prefix: str | None = None,
        environment: Environment | None = None,
        snapshot_depth: int = 0,
        snapshot_budget: list[int] | None = None,
        range_slots: bool = False,
        keyed: bool = False,
        ancestors: tuple[str, ...] = (),
    ) -> None:
        self.ns = ns
        self.values = values
        self.environment = environment or Environment()
        self.snapshot_depth = snapshot_depth
        self.snapshot_budget = snapshot_budget
        self.range_slots = range_slots
        self.keyed = keyed
        self.ancestors = ancestors
        self.out = out
        self.element_ids = 0
        # Scoped to this emitter, so one list item numbers its handlers across
        # all of its elements rather than restarting at each one.
        self.prefix = prefix
        self.scope_prefix = prefix if scope_prefix is None else scope_prefix
        self.handler_prefix = prefix if handler_prefix is None else handler_prefix
        self.handler_n = 0
        self.component_n = 0

    # -- helpers ----------------------------------------------------------

    def _value(self, hole: Hole) -> object:
        return self._lexical_value(hole, self.environment)

    def _lexical_value(self, hole: Hole, environment: Environment) -> object:
        try:
            return resolve(self.values[hole.index], environment)
        except KeyError as error:
            raise InterpolationError(str(error), hole.position) from error

    def _handler_id(self, hole_index: int, mount: str = "") -> str:
        """Top level: stable by hole index. Inside a list item: stable by
        (list, key, ordinal), so the same item keeps its ids across re-renders
        and a click arriving after a patch still resolves."""

        if not self.handler_prefix:
            return f"h{hole_index}"
        hid = f"h{self.handler_prefix}{mount}{self.handler_n}"
        self.handler_n += 1

        return hid

    def _next_element_id(self) -> str:
        self.element_ids += 1

        return f"{self.prefix}e{self.element_ids}"

    def _slot_markup(self, slot: str, markup: str) -> str:
        if self.range_slots:
            return OwnerPath(slot).wrap(markup, "slot")

        return f'<pysx-slot id="{_htmlmod.escape(slot, quote=True)}">{markup}</pysx-slot>'

    def _resolve(self, tag: str | Hole) -> tuple[str, list[object]]:
        found = self._value(tag) if isinstance(tag, Hole) else self.ns.get(tag)

        if isinstance(found, MissingComponent):
            raise NameError(f"unknown component {found.name!r}")

        if isinstance(found, ElementTag):
            return found.name, []

        if isinstance(found, StyledTag):
            self.out.styles.add(self.prefix, found.css_class, flatten(found.declarations))

            for _name, cls, body in found.variants:
                self.out.styles.add(self.prefix, cls, body)

            return found.tag, [found.css_class]

        if isinstance(tag, Hole):
            raise TypeError(
                f"component tag requires an element or callable; received {type(found).__name__}"
            )

        if not tag[:1].isupper():
            return tag, []

        raise NameError(f"unknown component {tag!r}")

    # -- nodes ------------------------------------------------------------

    def nodes(
        self, nodes: Sequence[Node], *, static: bool = False, root_classes: tuple[object, ...] = ()
    ) -> str:
        environment = self.environment

        try:
            return self._nodes(nodes, static=static, root_classes=root_classes)
        finally:
            self.environment = environment

    def _nodes(
        self, nodes: Sequence[Node], *, static: bool, root_classes: tuple[object, ...]
    ) -> str:
        parts: list[str] = []

        for node in nodes:
            if isinstance(node, str):
                parts.append(_htmlmod.escape(node))
            elif isinstance(node, Hole):
                parts.append(self.hole(node, static=static, root_classes=root_classes))
            elif isinstance(node, Conditional):
                parts.append(self.conditional(node, static=static, root_classes=root_classes))
            elif isinstance(node, Loop):
                parts.append(self.loop(node, static=static, root_classes=root_classes))
            elif isinstance(node, Local):
                value = self._value(node.value)

                if node.name is not None:
                    binding = self.ns.get(node.name)

                    if not isinstance(binding, LexicalBinding):
                        raise InterpolationError(
                            f"missing lexical binding {node.name!r}", node.value.position
                        )
                    declaration = cast("LexicalBinding[object]", binding)

                    if node.operation == "set":
                        try:
                            self.environment.get(declaration)
                        except KeyError as error:
                            raise InterpolationError(str(error), node.value.position) from error
                    self.environment = self.environment.child((declaration,), (value,))
            elif isinstance(node, Match):
                parts.append(self.match(node, static=static, root_classes=root_classes))
            else:
                parts.append(self.element(node, static=static, root_classes=root_classes))

        return "".join(parts)

    def hole(self, hole: Hole, *, static: bool, root_classes: tuple[object, ...] = ()) -> str:
        value: object

        if isinstance(self.values[hole.index], Deferred) and not static:
            environment = self.environment
            value = derived(lambda: _read(resolve(self.values[hole.index], environment)))
        else:
            value = self._value(hole)

        reader = _signal_reader(value)

        if reader is not None:
            current = _seed(reader)

            if _structured_content(current):
                if not static:
                    return self.content_slot(hole, reader, root_classes)
                value = current
            elif static:
                value = current

        if isinstance(value, (list, tuple)):
            return self.snapshot(hole, cast("Sequence[object]", value), root_classes)

        if isinstance(value, Children):
            sub = _Emitter(
                dict(value.namespace),
                value.values,
                self.out,
                prefix=f"{self.prefix}f{hole.index}:",
                scope_prefix=f"{self.scope_prefix}f{hole.index}:",
                environment=value.environment or self.environment,
                snapshot_depth=self.snapshot_depth,
                snapshot_budget=self.snapshot_budget,
                range_slots=self.range_slots,
                keyed=self.keyed,
                ancestors=self.ancestors,
            )

            return sub.nodes(list(value.nodes), static=static, root_classes=root_classes)

        if isinstance(value, (Fragment, Template)):
            fragment = value if isinstance(value, Fragment) else Fragment(value)
            namespace = fragment.scope_namespace(self.ns)
            owner = f"{self.prefix}f{hole.index}:"

            for name, body in fragment.rules:
                self.out.styles.add(owner, name, body)
            sub = _Emitter(
                namespace,
                _prepared_values(fragment.template),
                self.out,
                prefix=f"{self.prefix}f{hole.index}:",
                scope_prefix=f"{self.scope_prefix}f{hole.index}:",
                environment=self.environment,
                snapshot_depth=self.snapshot_depth,
                snapshot_budget=self.snapshot_budget,
                range_slots=self.range_slots,
                keyed=self.keyed,
                ancestors=self.ancestors,
            )

            return sub.nodes(
                _template_skeleton(fragment.template).root,
                static=static,
                root_classes=(*fragment.root_classes, *root_classes),
            )

        if isinstance(value, Each):
            return self.list_slot(
                hole, cast("Each[object]", value), static=static, root_classes=root_classes
            )
        slot = f"{self.prefix}{hole.index}"
        text = str(_seed_read(value))

        if isinstance(value, Signal) and not static:
            self.out.watchers.append(TextWatcher(slot, cast("Readable[object]", value), text))

        return self._slot_markup(slot, _htmlmod.escape(text))

    def snapshot(
        self, hole: Hole, entries: Sequence[object], root_classes: tuple[object, ...]
    ) -> str:
        if self.snapshot_budget is None:
            _validate_snapshot(entries)

        if self.snapshot_depth >= 128:
            raise ValueError("snapshot nesting exceeds 128 levels")
        budget = self.snapshot_budget if self.snapshot_budget is not None else [0]
        parts: list[str] = []

        for index, entry in enumerate(entries):
            budget[0] += 1

            if budget[0] > MAX_ROWS:
                raise ValueError("snapshot exceeds 10000 entries")
            prefix = f"{self.prefix}{hole.index}:snapshot:{index}:"
            sub = _Emitter(
                self.ns,
                (entry,),
                self.out,
                prefix=prefix,
                scope_prefix=f"{self.scope_prefix}{hole.index}:snapshot:{index}:",
                environment=self.environment,
                snapshot_depth=self.snapshot_depth + 1,
                snapshot_budget=budget,
                range_slots=self.range_slots,
                keyed=self.keyed,
                ancestors=self.ancestors,
            )
            parts.append(sub.hole(Hole(0), static=True, root_classes=root_classes))

        return "".join(parts)

    def content_slot(
        self, hole: Hole, source: Readable[object], root_classes: tuple[object, ...]
    ) -> str:
        slot = f"{self.prefix}{hole.index}"
        owner = f"{slot}:snapshot:"
        scope_owner = f"{self.scope_prefix}{hole.index}:snapshot:"
        environment = self.environment
        range_slots = self.range_slots
        ancestors = self.ancestors

        def content() -> str:
            self.out.revoke_refs(owner)
            self.out.styles.release(owner)

            for hid in tuple(self.out.handlers):
                if self.out.handler_owners.get(hid, "").startswith(owner):
                    self.out.handlers.pop(hid, None)
                    self.out.bindings.pop(hid, None)
                    self.out.bind_elements.pop(hid, None)
                    self.out.handler_owners.pop(hid, None)
                    self.out.event_handlers.pop(hid, None)
            value = source()
            entries = (
                cast("Sequence[object]", value) if isinstance(value, (list, tuple)) else (value,)
            )
            sub = _Emitter(
                self.ns,
                self.values,
                self.out,
                prefix=self.prefix,
                scope_prefix=self.scope_prefix,
                environment=environment,
                range_slots=range_slots,
                keyed=self.keyed,
                ancestors=ancestors,
            )
            with self.out.scopes.reconcile(scope_owner):
                return sub.snapshot(hole, entries, root_classes)

        markup = _seed(content)
        self.out.watchers.append(ContentWatcher(slot, content, markup))

        return self._slot_markup(slot, markup)

    def conditional(
        self, node: Conditional, *, static: bool, root_classes: tuple[object, ...] = ()
    ) -> str:
        value: object

        if isinstance(self.values[node.hole.index], Deferred) and not static:
            environment = self.environment
            value = derived(lambda: _read(resolve(self.values[node.hole.index], environment)))
        else:
            value = self._value(node.hole)
        slot = f"{self.prefix}{node.hole.index}"

        branch_base = f"{slot}:branch:"
        scope_base = f"{self.scope_prefix}{node.hole.index}:branch:"
        captured_environment = self.environment
        range_slots = self.range_slots
        ancestors = self.ancestors

        def branch(flag: bool) -> tuple[str, list[Watcher]]:
            previous = self.out.branches.get(slot)
            prefix = previous[1] if previous and previous[0] == flag else branch_base

            if previous and previous[0] != flag:
                self.out.release(previous[1], f"{scope_base}{int(not flag)}:")
                self.out.generation += 1
                prefix = f"{branch_base}g{self.out.generation}:"
            self.out.branches[slot] = (flag, prefix)
            scope_prefix = f"{scope_base}{int(flag)}:"
            self.out.scopes.release(f"{scope_base}{int(not flag)}:")

            if not self.keyed:
                self.out.revoke_refs(prefix)
            self.out.clear_owned(prefix)
            start = len(self.out.watchers)
            sub = _Emitter(
                self.ns,
                self.values,
                self.out,
                prefix=prefix,
                handler_prefix="" if not self.handler_prefix else prefix,
                scope_prefix=scope_prefix,
                environment=captured_environment,
                snapshot_depth=self.snapshot_depth,
                snapshot_budget=self.snapshot_budget,
                range_slots=range_slots,
                keyed=self.keyed,
                ancestors=ancestors,
            )
            with self.out.scopes.reconcile(scope_prefix):
                markup = sub.nodes(
                    node.then if flag else node.otherwise, static=static, root_classes=root_classes
                )
            watchers = self.out.watchers[start:]
            del self.out.watchers[start:]

            return markup, watchers

        flag = bool(_seed_read(value))
        markup, watchers = branch(flag)

        if isinstance(value, Signal) and not static:
            self.out.watchers.append(
                CondWatcher(slot, cast("Readable[object]", value), branch, flag, watchers)
            )
        else:
            self.out.watchers.extend(watchers)

        return self._slot_markup(slot, markup)

    def match(self, node: Match, *, static: bool, root_classes: tuple[object, ...]) -> str:
        environment = self.environment
        raw = self.values[node.value.index]
        live = not static and (
            _lexical_live(raw)
            or any(
                _lexical_live(self.values[pattern.index])
                for case in node.cases
                for pattern in case.patterns
                if isinstance(pattern, Hole)
            )
        )
        conditions: list[object] = []

        for case in node.cases:
            def selected(case: Case = case) -> bool:
                value = _read(resolve(raw, environment))

                return case.wildcard or any(
                    value == _read(resolve(self.values[pattern.index], environment))
                    if isinstance(pattern, Hole)
                    else value == pattern
                    for pattern in case.patterns
                )

            conditions.append(derived(selected) if live else selected())
        branch: tuple[Node, ...] = ()

        for index in reversed(range(len(node.cases))):
            branch = (
                Conditional(Hole(len(self.values) + index), node.cases[index].children, branch),
            )
        sub = _Emitter(
            self.ns,
            (*self.values, *conditions),
            self.out,
            prefix=f"{self.prefix}{node.value.index}:match:",
            scope_prefix=f"{self.scope_prefix}{node.value.index}:match:",
            environment=environment,
            snapshot_depth=self.snapshot_depth,
            snapshot_budget=self.snapshot_budget,
            range_slots=self.range_slots,
            keyed=self.keyed,
            ancestors=self.ancestors,
        )

        return sub.nodes(branch, static=static, root_classes=root_classes)

    def loop(self, node: Loop, *, static: bool, root_classes: tuple[object, ...]) -> str:
        declarations: list[LexicalBinding[object]] = []

        for name in node.names:
            binding = self.ns.get(name)

            if not isinstance(binding, LexicalBinding):
                raise InterpolationError(f"missing lexical binding {name!r}", node.source.position)
            declarations.append(cast("LexicalBinding[object]", binding))
        environment = self.environment
        raw = self.values[node.source.index]

        def items() -> Iterable[object]:
            source = _read(self._lexical_value(node.source, environment))

            if not isinstance(source, Iterable):
                raise TypeError("loop source must be iterable")
            rows: list[object] = []

            for index, row in enumerate(cast("Iterable[object]", source)):
                if index >= MAX_ROWS:
                    raise ValueError("loop exceeds 10000 rows")
                rows.append((index, row))

            return rows

        def row_environment(pair: object) -> Environment:
            _index, row = cast("tuple[int, object]", pair)
            values: tuple[object, ...]

            if len(declarations) == 1:
                values = (row,)
            else:
                if not isinstance(row, Iterable):
                    raise ValueError("destructured row must be iterable")
                values = tuple(islice(cast("Iterable[object]", row), len(declarations) + 1))

            return environment.child(tuple(declarations), values)

        def item(pair: object) -> Fragment:
            children = Children(node.children, self.values, self.ns, row_environment(pair))

            return pysx(Template("\n", Interpolation(children, "children")))

        def key(pair: object) -> str | int:
            if node.key is None:
                return cast("tuple[int, object]", pair)[0]
            value = _read(self._lexical_value(node.key, row_environment(pair)))

            if not isinstance(value, (str, int)) or isinstance(value, bool):
                raise InterpolationError("loop key must be a string or integer", node.key.position)

            return value

        live = _lexical_live(raw)
        source = derived(items) if live else items

        return self.list_slot(
            node.source,
            Each(source, item, key),
            static=static or not live,
            root_classes=root_classes,
        )

    def list_slot(
        self,
        hole: Hole,
        spec: Each[object],
        *,
        static: bool = False,
        root_classes: tuple[object, ...] = (),
    ) -> str:
        slot = f"{self.prefix}{hole.index}"
        scope_slot = f"{self.scope_prefix}{hole.index}"
        range_slots = self.range_slots
        ancestors = self.ancestors

        if not static and spec.live:
            def build(value: object, key: str, prefix: str, scope: str) -> str:
                return self.item(
                    spec,
                    value,
                    slot,
                    key,
                    scope_slot=scope_slot,
                    root_classes=root_classes,
                    range_slots=range_slots,
                    prefix_override=prefix,
                    scope_override=scope,
                    live=True,
                    ancestors=ancestors,
                )

            watcher = self.out.lists.get(slot)

            if watcher is None:
                watcher = ListWatcher(slot, scope_slot, spec.source, spec, build, self.out)
                self.out.lists[slot] = watcher
                with untracked():
                    initial = watcher.refresh()
                self.out.emit(
                    [op for op in initial if not (op["op"] == "list" and op["id"] == slot)]
                )
            else:
                with untracked():
                    watcher.rebind(spec, build)
            self.out.watchers.append(watcher)

            return OwnerPath(slot).wrap(
                "".join(watcher.markup[key] for key in watcher.order), "list"
            )

        def render_items(items: Iterable[object]) -> tuple[list[str], dict[str, str]]:
            rows: list[tuple[str, object]] = []
            keys: set[str] = set()

            for item in _bounded_items(items):
                raw_key = spec.key(item)

                if type(raw_key) not in (str, int):
                    raise TypeError("each keys must be strings or integers, excluding bool")
                key = str(raw_key)

                if key in keys:
                    raise ValueError(f"duplicate list key {key!r}")
                keys.add(key)
                rows.append((key, item))
            self.out.dom.revoke(f"{slot}:")
            self.out.styles.release(f"{slot}:")

            for hid in tuple(self.out.handlers):
                if hid.startswith(f"h{slot}:"):
                    self.out.handlers.pop(hid, None)
                    self.out.bindings.pop(hid, None)
                    self.out.bind_elements.pop(hid, None)
                    self.out.handler_owners.pop(hid, None)
                    self.out.event_handlers.pop(hid, None)
            order: list[str] = []
            markup: dict[str, str] = {}

            with self.out.scopes.reconcile(f"{scope_slot}:"):
                for key, item in rows:
                    order.append(key)
                    markup[key] = self.item(
                        spec,
                        item,
                        slot,
                        key,
                        scope_slot=scope_slot,
                        root_classes=root_classes,
                        range_slots=range_slots,
                    )

            return order, markup

        order, markup = render_items(spec.source())

        inner = "".join(markup[k] for k in order)

        return OwnerPath(slot).wrap(inner, "list")

    def item(
        self,
        spec: Each[object],
        item: object,
        slot: str,
        key: str,
        *,
        scope_slot: str,
        root_classes: tuple[object, ...] = (),
        range_slots: bool = False,
        prefix_override: str | None = None,
        scope_override: str | None = None,
        live: bool = False,
        ancestors: tuple[str, ...] | None = None,
    ) -> str:
        token = dom_context.set(self.out.dom)
        prefix = prefix_override or OwnerPath(slot).row(key).value
        scope = scope_override or OwnerPath(scope_slot).row(key).value
        owner_token = style_owner.set(prefix)

        try:
            with self.out.scopes.enter(scope, identity_for(spec.item)):
                fragment = spec.item(item)
        finally:
            dom_context.reset(token)
            style_owner.reset(owner_token)
        skeleton = _template_skeleton(fragment.template)

        for name, body in fragment.rules:
            self.out.styles.add(prefix, name, body)
        sub = _Emitter(
            fragment.scope_namespace(self.ns),
            _prepared_values(fragment.template),
            self.out,
            prefix=prefix,
            scope_prefix=scope,
            environment=self.environment,
            snapshot_depth=self.snapshot_depth,
            snapshot_budget=self.snapshot_budget,
            range_slots=range_slots,
            keyed=live,
            ancestors=self.ancestors if ancestors is None else ancestors,
        )
        markup = sub.nodes(
            skeleton.root, static=not live, root_classes=(*fragment.root_classes, *root_classes)
        )

        # Metadata never determines row boundaries; a row can have any number of roots.
        markup = re.sub(
            r"<([a-zA-Z][\w:-]*)(?=[\s/>])",
            lambda match: f'{match[0]} data-pysx-key="{_htmlmod.escape(key, quote=True)}"',
            markup,
            count=1,
        )

        return OwnerPath(slot).row(key).wrap(markup, "row")

    def call_component(
        self, fn: Component, el: Element, *, static: bool, root_classes: tuple[object, ...]
    ) -> str:
        props = {
            name: self._value(value) if isinstance(value, Hole) else value
            for name, value in el.attrs
        }

        if el.children:
            props["children"] = Children(
                tuple(el.children), self.values, dict(self.ns), self.environment
            )
        self.component_n += 1
        prefix = f"{self.prefix}c{self.component_n}:"
        scope_prefix = f"{self.scope_prefix}c{self.component_n}:"
        owner_token = style_owner.set(prefix)

        try:
            target = fn.base if isinstance(fn, StyledCallable) else fn
            with self.out.scopes.enter(scope_prefix, identity_for(target)):
                return self._component_body(
                    fn, target, el, props, prefix, scope_prefix, static, root_classes
                )
        finally:
            style_owner.reset(owner_token)

    def _component_body(
        self,
        fn: Component,
        target: Component,
        el: Element,
        props: dict[str, object],
        prefix: str,
        scope_prefix: str,
        static: bool,
        root_classes: tuple[object, ...],
    ) -> str:
        variant: VariantClass | None = None

        if isinstance(fn, StyledCallable):
            variant = VariantClass(fn.variants, props.pop("variant", None))
            _seed(variant)
            self.out.styles.apply((fn.css_class,))

            for _key, cls, body in fn.variants:
                self.out.styles.add(prefix, cls, body)
                self.out.styles.apply((cls,))
        children_parameter = signature(target, annotation_format=Format.STRING).parameters.get(
            "children"
        )

        if (
            el.children
            and children_parameter is not None
            and children_parameter.kind is Parameter.VAR_POSITIONAL
        ):
            result = fn(props.pop("children"), **props)
        else:
            result = fn(**props)

        if not isinstance(result, (Template, Fragment)):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("components must return Template or Fragment")
        fragment = result if isinstance(result, Fragment) else Fragment(result)
        namespace = fragment.scope_namespace(namespace_for(fn))

        for name, body in fragment.rules:
            self.out.styles.add(prefix, name, body)
        sub = _Emitter(
            namespace,
            _prepared_values(fragment.template),
            self.out,
            prefix=prefix,
            scope_prefix=scope_prefix,
            environment=self.environment,
            snapshot_depth=self.snapshot_depth,
            snapshot_budget=self.snapshot_budget,
            range_slots=self.range_slots,
            keyed=self.keyed,
            ancestors=self.ancestors,
        )

        return sub.nodes(
            _template_skeleton(fragment.template).root,
            static=static,
            root_classes=(*fragment.root_classes, *root_classes, *((variant,) if variant else ())),
        )

    def element(self, el: Element, *, static: bool, root_classes: tuple[object, ...] = ()) -> str:
        found = self._value(el.tag) if isinstance(el.tag, Hole) else self.ns.get(el.tag)

        if (
            callable(found)
            and not isinstance(found, StyledTag)
            and (isinstance(el.tag, Hole) or el.tag not in NATIVE_TAGS)
        ):
            return self.call_component(
                cast("Component", found), el, static=static, root_classes=root_classes
            )
        tag, scoped = self._resolve(el.tag)
        classes: list[object] = list(scoped)
        classes.extend(root_classes)

        if isinstance(found, StyledTag):
            variant = next((value for name, value in el.attrs if name == "variant"), None)
            source = self._value(variant) if isinstance(variant, Hole) else variant
            classes.append(VariantClass(found.variants, source))

        if tag == "fragment":
            if el.attrs:
                raise ValueError("fragment does not accept DOM attributes")

            return self.nodes(el.children, static=static, root_classes=(*scoped, *root_classes))
        self.out.styles.apply(_seed_class(value) for value in classes if _seed_class(value))

        if isinstance(found, StyledTag):
            self.out.styles.apply(cls for _name, cls, _body in found.variants)
        attrs: list[str] = []
        typed_types: list[str] = []
        element_id: str | None = self._next_element_id() if self.keyed else None
        initial_value: str | None = None
        raw_attributes = {
            name: self._value(value) if isinstance(value, Hole) else value
            for name, value in el.attrs
        }
        mount = ""

        if element_id and self.keyed:
            scope = active_scope()
            ref_value = raw_attributes.get("ref")
            identity = (
                *self.ancestors,
                tag,
                el.namespace,
                scope.token if scope else "",
                str(isinstance(ref_value, DomRef) and ref_value.imperative),
            )
            previous_element = self.out.elements.get(element_id)

            if previous_element:
                if previous_element[0] == identity:
                    mount = previous_element[1]
                else:
                    self.out.generation += 1
                    mount = f"m{self.out.generation}:"
            elif element_id in self.out.retired:
                self.out.generation += 1
                mount = f"m{self.out.generation}:"
            self.out.elements[element_id] = (identity, mount)

        if sum(attr_kind(name) is HoleKind.BIND for name, _value in el.attrs) > 1:
            raise TypeError("a form control accepts exactly one binding")
        control_type = str(_seed_read(raw_attributes.get("type", "text")))
        multiple = _seed_attr(raw_attributes.get("multiple"), "multiple") is not None
        normalized = {normalize_attr(name): value for name, value in raw_attributes.items()}
        variables = normalized.get("stylevars", normalized.get("cssvars"))

        if "css" in normalized:
            source = normalized["css"]
            element_id = element_id or self._next_element_id()
            owner = f"{self.prefix}css:{element_id}"
            self.out.styles.replace(owner, _seed(lambda: css_text(source)))
            classes.append(CssClass(source))

            if isinstance(source, Signal) and not static:
                self.out.watchers.append(
                    CssWatcher(self.out.styles, owner, cast("Signal[object]", source))
                )

        if variables is not None:
            element_id = element_id or self._next_element_id()
            style_watcher = InlineStyleWatcher(element_id, normalized.get("style"), variables)
            style_watcher.last = _seed(style_watcher.value)

            if style_watcher.last:
                attrs.append(f' style="{_htmlmod.escape(style_watcher.last, quote=True)}"')

            if not static:
                self.out.watchers.append(style_watcher)

        for name, value in el.attrs:
            if name == "variant" and isinstance(found, StyledTag):
                continue
            kind = self._kind(name)
            raw_name = name
            name = normalize_attr(name) if el.namespace == "html" else name

            if normalize_attr(name) in {"css", "stylevars", "cssvars"}:
                continue

            if name == "style" and variables is not None:
                continue

            if not isinstance(value, Hole):
                if kind is HoleKind.EVENT:
                    raise TypeError("event attributes require an interpolated callable")
                text = _attr_text(value, name)

                if name == "class":
                    classes.append(value)
                elif text is not None:
                    attrs.append(f' {name}="{_htmlmod.escape(text, quote=True)}"')

                if name == "value":
                    initial_value = text

                continue

            raw = self._value(value)

            if name == "ref":
                if not isinstance(raw, DomRef):
                    raise TypeError("ref requires a DomRef")

                if self.keyed:
                    element_id = element_id or self._next_element_id()
                    scope = active_scope()
                    signature_path = (
                        *self.ancestors,
                        tag,
                        el.namespace,
                        str(raw.imperative),
                        scope.token if scope else "",
                    )
                    previous = self.out.refs.get(element_id)

                    if (
                        previous
                        and previous[0] == signature_path
                        and previous[1].token in self.out.dom.mounts
                    ):
                        if raw.controller is not self.out.dom:
                            raise DomError("ref belongs to another render")

                        if raw.token in self.out.dom.mounts and raw.token != previous[1].token:
                            raise DomError("a ref can mount on only one element")
                        raw.token = previous[1].token
                        token = raw.token
                    else:
                        self.out.dom.revoke(f"{element_id}:ref:")
                        token = self.out.dom.mount(raw, f"{element_id}:ref:")
                    self.out.refs[element_id] = (signature_path, raw)
                else:
                    token = self.out.dom.mount(raw, self.prefix)
                attrs.append(f' data-pysx-ref="{token}"')

                if raw.imperative:
                    if el.children:
                        raise DomError("imperative zones must have no reactive children")
                    attrs.append(' data-pysx-imperative="true"')

                continue

            if kind is HoleKind.EVENT:
                hid = self._handler_id(value.index, mount)
                event_type = name[2:].lower()

                if isinstance(raw, EventHandler):
                    policy = raw.policy(event_type)
                    event_type = raw.event_type or event_type
                    self.out.event_handlers[hid] = (raw, event_type)
                    self.out.handlers[hid] = lambda _value: None
                    self.out.handler_owners[hid] = self.prefix
                    encoded = _htmlmod.escape(json.dumps(policy), quote=True)
                    attrs.append(f' data-pysx-policy-{event_type}="{encoded}"')
                    typed_types.append(event_type)
                    attrs.append(f' data-pysx-{event_type}="{_htmlmod.escape(hid, quote=True)}"')

                    continue

                if not callable(raw):
                    raise TypeError("event hole requires a callable")
                self.out.handlers[hid] = cast("Callable[[object], object]", raw)
                self.out.handler_owners[hid] = self.prefix
                attrs.append(f' data-pysx-{name[2:].lower()}="{_htmlmod.escape(hid, quote=True)}"')

            elif kind is HoleKind.BIND:
                hid = self._handler_id(value.index, mount)
                element_id = element_id or self._next_element_id()
                radio = raw_attributes.get("value")
                with untracked():
                    binding = make_binding(
                        raw_name,
                        tag,
                        control_type,
                        multiple,
                        raw,
                        element_id,
                        str(_seed_read(radio)) if radio is not None else None,
                    )
                self.out.handlers[hid] = binding.set
                self.out.handler_owners[hid] = self.prefix
                self.out.bindings[hid] = binding
                self.out.bind_elements[hid] = element_id
                current = _seed(binding.value)
                escaped_hid = _htmlmod.escape(hid, quote=True)
                attrs.append(f' data-pysx-bind="{binding.mode}"')
                attrs.append(f' data-pysx-binding="{escaped_hid}"')
                event = "input" if binding.mode == "value" and tag != "select" else "change"
                attrs.append(f' data-pysx-bind-event="{event}"')

                if not any(normalize_attr(name) == "on" + event for name in raw_attributes):
                    attrs.append(f' data-pysx-{event}="{escaped_hid}"')

                if binding.mode == "selected":
                    attrs.append(
                        f' data-pysx-selected="{_htmlmod.escape(json.dumps(current), quote=True)}"'
                    )
                elif isinstance(current, bool):
                    if current:
                        attrs.append(" checked")
                elif isinstance(current, str):
                    initial_value = current
                    attrs.append(f' value="{_htmlmod.escape(current, quote=True)}"')

                if not static:
                    self.out.watchers.append(BindingWatcher(binding, current))

            else:  # ATTR
                text = _seed_class(raw) if name == "class" else _seed_attr(raw, name)

                if name == "value":
                    initial_value = text

                if name == "class":
                    classes.append(raw)

                    continue

                if isinstance(raw, Signal) and not static:
                    element_id = element_id or self._next_element_id()
                    self.out.watchers.append(
                        AttrWatcher(element_id, name, cast("Readable[object]", raw), text)
                    )

                if text is None:
                    pass
                elif text == "":
                    attrs.append(f" {name}")
                else:
                    attrs.append(f' {name}="{_htmlmod.escape(text, quote=True)}"')

        if not static and any(
            isinstance(value, (Signal, CssClass))
            or (isinstance(value, VariantClass) and isinstance(value.source, Signal))
            for value in classes
        ):
            element_id = element_id or self._next_element_id()
            watcher = ClassWatcher(
                element_id,
                tuple(classes),
                "",
            )
            # Render and baseline come from one function, so they cannot drift.
            watcher.last = _seed(watcher.merged)
            self.out.watchers.append(watcher)
            class_attr = watcher.last
        else:
            class_attr = _merge_classes(_seed_class(value) for value in classes)

        if class_attr:
            attrs.insert(0, f' class="{_htmlmod.escape(class_attr, quote=True)}"')

        if element_id:
            attrs.append(f' data-pysx-el="{_htmlmod.escape(element_id, quote=True)}"')

        if typed_types:
            joined = _htmlmod.escape(" ".join(dict.fromkeys(typed_types)), quote=True)
            attrs.append(f' data-pysx-typed="{joined}"')

        open_tag = f"<{tag}{''.join(attrs)}>"

        if tag in VOID:
            return open_tag
        previous_context = self.range_slots
        previous_ancestors = self.ancestors
        self.ancestors = (*self.ancestors, tag)
        self.range_slots = previous_context or tag in {
            "table",
            "thead",
            "tbody",
            "tfoot",
            "tr",
            "colgroup",
            "select",
            "optgroup",
            "option",
            "svg",
            "math",
        }

        try:
            content = (
                _htmlmod.escape(initial_value)
                if tag == "textarea" and initial_value is not None
                else self.nodes(el.children, static=static)
            )
        finally:
            self.range_slots = previous_context
            self.ancestors = previous_ancestors

        return open_tag + content + f"</{tag}>"

    def _kind(self, name: str) -> HoleKind:
        return attr_kind(name)


def render(
    component_fn: Callable[[], Template | Fragment],
    *,
    namespace: Mapping[str, object] | None = None,
) -> Rendered:
    out = Rendered()
    out.styles.shared = global_rules()
    token = dom_context.set(out.dom)
    style_token = style_context.set(out.styles)

    try:
        with out.scopes.enter("", identity_for(component_fn)):
            result = component_fn()

        if not isinstance(result, (Template, Fragment)):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError("components must return Template or Fragment")
        fragment = result if isinstance(result, Fragment) else Fragment(result)

        if fragment.themes is not None:
            theme_watcher = ThemeWatcher(out.styles, fragment.themes)
            theme_watcher.refresh()
            out.watchers.append(theme_watcher)

        for name, body in fragment.rules:
            out.styles.add("", name, body)
        skeleton = _template_skeleton(fragment.template)
        emitter = _Emitter(
            fragment.scope_namespace(namespace_for(component_fn) | dict(namespace or {})),
            _prepared_values(fragment.template),
            out,
        )
        out.body = emitter.nodes(skeleton.root, root_classes=fragment.root_classes)
    except BaseException as error:
        try:
            out.dispose()
        except Exception as cleanup_error:
            error.add_note(f"render cleanup also failed: {cleanup_error}")

        raise
    finally:
        dom_context.reset(token)
        style_context.reset(style_token)
    out.css = out.styles.snapshot()

    return out
