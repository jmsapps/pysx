"""Node tree -> HTML plus a set of watchers that produce wire ops.

Each reactive hole gets a Watcher. A watcher reads its signal (which registers
the subscription) and emits an op only when the value actually changed, so an
event that touches nothing sends no frame.

List items are re-rendered as whole HTML strings rather than patched slot by
slot. Keyed identity is still preserved: an item whose HTML is unchanged is not
sent and its DOM node is never touched.
"""

from __future__ import annotations

import html as _htmlmod
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from string.templatelib import Template
from typing import TYPE_CHECKING, cast

from .elements import VOID, ElementTag
from .forms import Binding, make_binding
from .parser import Conditional, Element, Hole, HoleKind, Node, Skeleton, attr_kind, parse
from .reactive import Readable, Signal
from .schema import BOOLEAN_ATTRS, normalize_attr, resolve_tag
from .styled import StyledTag, stylesheet

if TYPE_CHECKING:
    from .wire import Op


@dataclass(frozen=True)
class Fragment:
    template: Template


def html(template: Template) -> Fragment:
    if not isinstance(template, Template):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError('html() takes a t-string; did you write f""" instead of t"""?')

    return Fragment(template)


def component[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    setattr(fn, "_pysx_component", True)  # noqa: B010 - dynamic compatibility marker

    return fn


@dataclass(frozen=True)
class Each[T]:
    source: Readable[Iterable[T]]
    item: Callable[[T], Fragment]
    key: Callable[[T], object]


def each[T](
    source: Readable[Iterable[T]], item: Callable[[T], Fragment], *, key: Callable[[T], object]
) -> Each[T]:
    return Each(source, item, key)


def _read(value: object) -> object:
    return cast("Readable[object]", value)() if isinstance(value, Signal) else value


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
        markup, self.children = self.render_branch(now)

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
    signal: Readable[object]
    before: tuple[str, ...]
    after: tuple[str, ...]
    last: str

    def merged(self) -> str:
        value = _class_text(self.signal)

        return _merge_classes([*self.before, value, *self.after])

    def refresh(self) -> list[Op]:
        now = self.merged()

        if now == self.last:
            return []
        self.last = now

        return [{"op": "attr", "id": self.element, "name": "class", "v": now}]


@dataclass
class ListWatcher(Watcher):
    slot: str
    signal: Readable[Iterable[object]]
    spec: Each[object]
    render_items: Callable[[Iterable[object]], tuple[list[str], dict[str, str]]]
    order: list[str]
    markup: dict[str, str]

    def refresh(self) -> list[Op]:
        order, markup = self.render_items(self.signal())
        changed = {k: v for k, v in markup.items() if self.markup.get(k) != v}

        if order == self.order and not changed:
            return []
        self.order, self.markup = order, markup

        return [{"op": "list", "id": self.slot, "keys": order, "html": changed}]


@dataclass
class Rendered:
    body: str = ""
    css: str = ""
    handlers: dict[str, Callable[[object], object]] = field(
        default_factory=dict[str, Callable[[object], object]]
    )
    watchers: list[Watcher] = field(default_factory=list[Watcher])
    # handler id -> element id, so an input's own event can be stopped from
    # echoing a value patch back and resetting the caret.
    bind_elements: dict[str, str] = field(default_factory=dict[str, str])
    bindings: dict[str, Binding] = field(default_factory=dict[str, Binding])
    handler_owners: dict[str, str] = field(default_factory=dict[str, str])


# --------------------------------------------------------------------------- emit


class _Emitter:
    def __init__(
        self,
        ns: dict[str, object],
        values: tuple[object, ...],
        out: Rendered,
        prefix: str = "",
        handler_prefix: str | None = None,
    ) -> None:
        self.ns = ns
        self.values = values
        self.out = out
        self.element_ids = 0
        # Scoped to this emitter, so one list item numbers its handlers across
        # all of its elements rather than restarting at each one.
        self.prefix = prefix
        self.handler_prefix = prefix if handler_prefix is None else handler_prefix
        self.handler_n = 0

    # -- helpers ----------------------------------------------------------

    def _handler_id(self, hole_index: int) -> str:
        """Top level: stable by hole index. Inside a list item: stable by
        (list, key, ordinal), so the same item keeps its ids across re-renders
        and a click arriving after a patch still resolves."""

        if not self.handler_prefix:
            return f"h{hole_index}"
        hid = f"h{self.handler_prefix}{self.handler_n}"
        self.handler_n += 1

        return hid

    def _next_element_id(self) -> str:
        self.element_ids += 1

        return f"{self.prefix}e{self.element_ids}"

    def _resolve(self, tag: str) -> tuple[str, list[str]]:
        if not tag[:1].isupper():
            return resolve_tag(tag), []
        found = self.ns.get(tag)

        if isinstance(found, ElementTag):
            return found.name, []

        if isinstance(found, StyledTag):
            return found.tag, [found.css_class]

        raise NameError(f"unknown component {tag!r}")

    # -- nodes ------------------------------------------------------------

    def nodes(self, nodes: list[Node], *, static: bool = False) -> str:
        parts: list[str] = []

        for node in nodes:
            if isinstance(node, str):
                parts.append(_htmlmod.escape(node))
            elif isinstance(node, Hole):
                parts.append(self.hole(node, static=static))
            elif isinstance(node, Conditional):
                parts.append(self.conditional(node, static=static))
            else:
                parts.append(self.element(node, static=static))

        return "".join(parts)

    def hole(self, hole: Hole, *, static: bool) -> str:
        value = self.values[hole.index]

        if isinstance(value, Fragment):
            sub = _Emitter(
                self.ns, value.template.values, self.out, prefix=f"{self.prefix}f{hole.index}:"
            )

            return sub.nodes(parse(value.template.strings).root, static=static)

        if isinstance(value, Each):
            return self.list_slot(hole, cast("Each[object]", value))
        slot = f"{self.prefix}{hole.index}"
        text = str(_read(value))

        if isinstance(value, Signal) and not static:
            self.out.watchers.append(TextWatcher(slot, cast("Readable[object]", value), text))
        escaped_slot = _htmlmod.escape(slot, quote=True)

        return f'<pysx-slot id="{escaped_slot}">{_htmlmod.escape(text)}</pysx-slot>'

    def conditional(self, node: Conditional, *, static: bool) -> str:
        value = self.values[node.hole.index]
        slot = f"{self.prefix}{node.hole.index}"

        prefix = f"{slot}:branch:"

        def branch(flag: bool) -> tuple[str, list[Watcher]]:
            for hid in tuple(self.out.handlers):
                if self.out.handler_owners.get(hid, "").startswith(prefix):
                    self.out.handlers.pop(hid, None)
                    self.out.bindings.pop(hid, None)
                    self.out.bind_elements.pop(hid, None)
                    self.out.handler_owners.pop(hid, None)
            start = len(self.out.watchers)
            sub = _Emitter(
                self.ns,
                self.values,
                self.out,
                prefix=prefix,
                handler_prefix="" if not self.handler_prefix else prefix,
            )
            markup = sub.nodes(node.then if flag else node.otherwise, static=static)
            watchers = self.out.watchers[start:]
            del self.out.watchers[start:]

            return markup, watchers

        flag = bool(_read(value))
        markup, watchers = branch(flag)

        if isinstance(value, Signal) and not static:
            self.out.watchers.append(
                CondWatcher(slot, cast("Readable[object]", value), branch, flag, watchers)
            )
        else:
            self.out.watchers.extend(watchers)

        return f'<pysx-slot id="{_htmlmod.escape(slot, quote=True)}">{markup}</pysx-slot>'

    def list_slot(self, hole: Hole, spec: Each[object]) -> str:
        slot = f"{self.prefix}{hole.index}"

        def render_items(items: Iterable[object]) -> tuple[list[str], dict[str, str]]:
            for hid in tuple(self.out.handlers):
                if hid.startswith(f"h{slot}:"):
                    self.out.handlers.pop(hid, None)
                    self.out.bindings.pop(hid, None)
                    self.out.bind_elements.pop(hid, None)
                    self.out.handler_owners.pop(hid, None)
            order: list[str] = []
            markup: dict[str, str] = {}

            for item in items:
                key = str(spec.key(item))

                if key in markup:
                    raise ValueError(f"duplicate list key {key!r}")
                order.append(key)
                markup[key] = self.item(spec, item, slot, key)

            return order, markup

        order, markup = render_items(spec.source())
        self.out.watchers.append(ListWatcher(slot, spec.source, spec, render_items, order, markup))
        inner = "".join(markup[k] for k in order)

        return f'<pysx-list id="{_htmlmod.escape(slot, quote=True)}">{inner}</pysx-list>'

    def item(self, spec: Each[object], item: object, slot: str, key: str) -> str:
        fragment = spec.item(item)
        skeleton = parse(fragment.template.strings)
        sub = _Emitter(self.ns, fragment.template.values, self.out, prefix=f"{slot}:{key}:")
        markup = sub.nodes(skeleton.root, static=True)

        # Tag the item root so the client can reorder by key.
        return markup.replace(">", f' data-pysx-key="{_htmlmod.escape(key, quote=True)}">', 1)

    def element(self, el: Element, *, static: bool) -> str:
        tag, classes = self._resolve(el.tag)

        if tag == "fragment":
            if el.attrs:
                raise ValueError("fragment does not accept DOM attributes")

            return self.nodes(el.children, static=static)
        attrs: list[str] = []
        element_id: str | None = None
        class_signal: Readable[object] | None = None
        class_position = -1
        initial_value: str | None = None
        raw_attributes = {
            name: self.values[value.index] if isinstance(value, Hole) else value
            for name, value in el.attrs
        }

        if sum(attr_kind(name) is HoleKind.BIND for name, _value in el.attrs) > 1:
            raise TypeError("a form control accepts exactly one binding")
        control_type = str(_read(raw_attributes.get("type", "text")))
        multiple = _attr_text(raw_attributes.get("multiple"), "multiple") is not None

        for name, value in el.attrs:
            kind = self._kind(name)
            raw_name = name
            name = normalize_attr(name) if el.namespace == "html" else name

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

            raw = self.values[value.index]

            if kind is HoleKind.EVENT:
                hid = self._handler_id(value.index)

                if not callable(raw):
                    raise TypeError("event hole requires a callable")
                self.out.handlers[hid] = cast("Callable[[object], object]", raw)
                self.out.handler_owners[hid] = self.prefix
                attrs.append(f' data-pysx-{name[2:].lower()}="{_htmlmod.escape(hid, quote=True)}"')

            elif kind is HoleKind.BIND:
                hid = self._handler_id(value.index)
                element_id = element_id or self._next_element_id()
                radio = raw_attributes.get("value")
                binding = make_binding(
                    raw_name,
                    tag,
                    control_type,
                    multiple,
                    raw,
                    element_id,
                    str(_read(radio)) if radio is not None else None,
                )
                self.out.handlers[hid] = binding.set
                self.out.handler_owners[hid] = self.prefix
                self.out.bindings[hid] = binding
                self.out.bind_elements[hid] = element_id
                current = binding.value()
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
                text = _class_text(raw) if name == "class" else _attr_text(raw, name)

                if name == "value":
                    initial_value = text

                if name == "class":
                    if isinstance(raw, Signal) and not static:
                        # Recorded as an insertion point, not appended: the
                        # watcher supplies this slot's value on every refresh.
                        class_signal = cast("Readable[object]", raw)
                        class_position = len(classes)
                    elif text:
                        classes.append(text)

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

        if class_signal is not None:
            element_id = element_id or self._next_element_id()
            watcher = ClassWatcher(
                element_id,
                class_signal,
                tuple(classes[:class_position]),
                tuple(classes[class_position:]),
                "",
            )
            # Render and baseline come from one function, so they cannot drift.
            watcher.last = watcher.merged()
            self.out.watchers.append(watcher)
            class_attr = watcher.last
        else:
            class_attr = _merge_classes(classes)

        if class_attr:
            attrs.insert(0, f' class="{_htmlmod.escape(class_attr, quote=True)}"')

        if element_id:
            attrs.append(f' data-pysx-el="{_htmlmod.escape(element_id, quote=True)}"')

        open_tag = f"<{tag}{''.join(attrs)}>"

        if tag in VOID:
            return open_tag
        content = (
            _htmlmod.escape(initial_value)
            if tag == "textarea" and initial_value is not None
            else self.nodes(el.children, static=static)
        )

        return open_tag + content + f"</{tag}>"

    def _kind(self, name: str) -> HoleKind:
        return attr_kind(name)


def render(component_fn: Callable[[], Fragment]) -> Rendered:
    fragment = component_fn()
    skeleton: Skeleton = parse(fragment.template.strings)
    out = Rendered()
    emitter = _Emitter(
        cast("dict[str, object]", component_fn.__globals__), fragment.template.values, out
    )
    out.body = emitter.nodes(skeleton.root)
    out.css = stylesheet()

    return out
