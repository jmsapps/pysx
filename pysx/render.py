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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from string.templatelib import Template
from typing import Any

from .elements import VOID
from .parser import Conditional, Element, Hole, HoleKind, Skeleton, parse
from .reactive import Signal
from .styled import StyledTag, stylesheet


@dataclass(frozen=True)
class Fragment:
    template: Template


def html(template: Template) -> Fragment:
    if not isinstance(template, Template):
        raise TypeError('html() takes a t-string; did you write f""" instead of t"""?')
    return Fragment(template)


def component(fn):
    fn._pysx_component = True
    return fn


@dataclass(frozen=True)
class Each:
    source: Signal
    item: Callable[[Any], Fragment]
    key: Callable[[Any], Any]


def each(
    source: Signal, item: Callable[[Any], Fragment], *, key: Callable[[Any], Any]
) -> Each:
    return Each(source, item, key)


def _escape(value: Any) -> str:
    return _htmlmod.escape(str(value))


def _read(value: Any) -> Any:
    return value() if isinstance(value, Signal) else value


def _attr_text(value: Any) -> str | None:
    """None means the attribute is absent; True renders it bare."""
    value = _read(value)
    if value is None or value is False:
        return None
    if value is True:
        return ""
    return str(value)


# --------------------------------------------------------------------------- watchers


class Watcher:
    signal: Signal

    def refresh(self) -> list[dict]:
        raise NotImplementedError


@dataclass
class TextWatcher(Watcher):
    slot: str
    signal: Signal
    last: str

    def refresh(self) -> list[dict]:
        now = str(self.signal())
        if now == self.last:
            return []
        self.last = now
        return [{"op": "text", "id": self.slot, "v": now}]


@dataclass
class CondWatcher(Watcher):
    slot: str
    signal: Signal
    render_branch: Callable[[bool], str]
    last: str

    def refresh(self) -> list[dict]:
        now = self.render_branch(bool(self.signal()))
        if now == self.last:
            return []
        self.last = now
        return [{"op": "html", "id": self.slot, "v": now}]


@dataclass
class AttrWatcher(Watcher):
    element: str
    name: str
    signal: Signal
    last: str | None

    def refresh(self) -> list[dict]:
        now = _attr_text(self.signal)
        if now == self.last:
            return []
        self.last = now
        return [{"op": "attr", "id": self.element, "name": self.name, "v": now}]


@dataclass
class ClassWatcher(Watcher):
    """`class` is merged from several sources — the styled hash, any literal
    class, and the hole. Patching only the hole's value would replace the whole
    attribute and strip the scoped class, killing the element's styling."""

    element: str
    signal: Signal
    before: tuple[str, ...]
    after: tuple[str, ...]
    last: str

    def merged(self) -> str:
        value = _attr_text(self.signal) or ""
        return " ".join(" ".join([*self.before, value, *self.after]).split())

    def refresh(self) -> list[dict]:
        now = self.merged()
        if now == self.last:
            return []
        self.last = now
        return [{"op": "attr", "id": self.element, "name": "class", "v": now}]


@dataclass
class ListWatcher(Watcher):
    slot: str
    signal: Signal
    spec: Each
    render_items: Callable[[Iterable[Any]], tuple[list[str], dict[str, str]]]
    order: list[str]
    markup: dict[str, str]

    def refresh(self) -> list[dict]:
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
    handlers: dict[str, Callable] = field(default_factory=dict)
    watchers: list[Watcher] = field(default_factory=list)
    # handler id -> element id, so an input's own event can be stopped from
    # echoing a value patch back and resetting the caret.
    bind_elements: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- emit


class _Emitter:
    def __init__(
        self, ns: dict, values: tuple, out: Rendered, prefix: str = ""
    ) -> None:
        self.ns = ns
        self.values = values
        self.out = out
        self.element_ids = 0
        # Scoped to this emitter, so one list item numbers its handlers across
        # all of its elements rather than restarting at each one.
        self.prefix = prefix
        self.handler_n = 0

    # -- helpers ----------------------------------------------------------

    def _handler_id(self, hole_index: int) -> str:
        """Top level: stable by hole index. Inside a list item: stable by
        (list, key, ordinal), so the same item keeps its ids across re-renders
        and a click arriving after a patch still resolves."""
        if not self.prefix:
            return f"h{hole_index}"
        hid = f"h{self.prefix}{self.handler_n}"
        self.handler_n += 1
        return hid

    def _next_element_id(self) -> str:
        self.element_ids += 1
        return f"e{self.element_ids}"

    def _resolve(self, tag: str) -> tuple[str, list[str]]:
        if not tag[:1].isupper():
            return tag, []
        found = self.ns.get(tag)
        if isinstance(found, StyledTag):
            return found.tag, [found.css_class]
        raise NameError(f"unknown component {tag!r}")

    # -- nodes ------------------------------------------------------------

    def nodes(self, nodes: list, *, static: bool = False) -> str:
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
        if isinstance(value, Each):
            return self.list_slot(hole, value)
        slot = f"{self.prefix}{hole.index}"
        text = str(_read(value))
        if isinstance(value, Signal) and not static:
            self.out.watchers.append(TextWatcher(slot, value, text))
        return f'<pysx-slot id="{slot}">{_htmlmod.escape(text)}</pysx-slot>'

    def conditional(self, node: Conditional, *, static: bool) -> str:
        value = self.values[node.hole.index]
        slot = f"{self.prefix}{node.hole.index}"

        def branch(flag: bool) -> str:
            return self.nodes(node.then if flag else node.otherwise, static=True)

        markup = branch(bool(_read(value)))
        if isinstance(value, Signal) and not static:
            self.out.watchers.append(CondWatcher(slot, value, branch, markup))
        return f'<pysx-slot id="{slot}">{markup}</pysx-slot>'

    def list_slot(self, hole: Hole, spec: Each) -> str:
        slot = f"{self.prefix}{hole.index}"

        def render_items(items) -> tuple[list[str], dict[str, str]]:
            order: list[str] = []
            markup: dict[str, str] = {}
            for item in items:
                key = str(spec.key(item))
                order.append(key)
                markup[key] = self.item(spec, item, slot, key)
            return order, markup

        order, markup = render_items(spec.source())
        self.out.watchers.append(
            ListWatcher(slot, spec.source, spec, render_items, order, markup)
        )
        inner = "".join(markup[k] for k in order)
        return f'<pysx-list id="{slot}">{inner}</pysx-list>'

    def item(self, spec: Each, item: Any, slot: str, key: str) -> str:
        fragment = spec.item(item)
        skeleton = parse(fragment.template.strings)
        sub = _Emitter(
            self.ns, fragment.template.values, self.out, prefix=f"{slot}:{key}:"
        )
        markup = sub.nodes(skeleton.root, static=True)
        # Tag the item root so the client can reorder by key.
        return markup.replace(">", f' data-pysx-key="{key}">', 1)

    def element(self, el: Element, *, static: bool) -> str:
        tag, classes = self._resolve(el.tag)
        attrs: list[str] = []
        element_id: str | None = None
        class_signal: Signal | None = None
        class_position = -1

        for name, value in el.attrs:
            if not isinstance(value, Hole):
                if name == "class":
                    classes.append(value)
                else:
                    attrs.append(f' {name}="{_htmlmod.escape(value, quote=True)}"')
                continue

            raw = self.values[value.index]
            kind = self._kind(name)

            if kind is HoleKind.EVENT:
                hid = self._handler_id(value.index)
                self.out.handlers[hid] = raw
                attrs.append(f' data-pysx-{name[2:].lower()}="{hid}"')

            elif kind is HoleKind.BIND:
                hid = self._handler_id(value.index)
                signal = raw
                self.out.handlers[hid] = lambda e, s=signal: s.set(e)
                current = str(_read(signal))
                attrs.append(f' value="{_htmlmod.escape(current, quote=True)}"')
                attrs.append(f' data-pysx-input="{hid}"')
                if isinstance(signal, Signal) and not static:
                    element_id = element_id or self._next_element_id()
                    self.out.bind_elements[hid] = element_id
                    self.out.watchers.append(
                        AttrWatcher(element_id, "value", signal, current)
                    )

            else:  # ATTR
                text = _attr_text(raw)
                if name == "class":
                    if isinstance(raw, Signal) and not static:
                        # Recorded as an insertion point, not appended: the
                        # watcher supplies this slot's value on every refresh.
                        class_signal = raw
                        class_position = len(classes)
                    elif text:
                        classes.append(text)
                    continue
                if isinstance(raw, Signal) and not static:
                    element_id = element_id or self._next_element_id()
                    self.out.watchers.append(AttrWatcher(element_id, name, raw, text))
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
            class_attr = " ".join(" ".join(classes).split())
        if class_attr:
            attrs.insert(0, f' class="{class_attr}"')
        if element_id:
            attrs.append(f' data-pysx-el="{element_id}"')

        open_tag = f"<{tag}{''.join(attrs)}>"
        if tag in VOID:
            return open_tag
        return open_tag + self.nodes(el.children, static=static) + f"</{tag}>"

    def _kind(self, name: str) -> HoleKind:
        if len(name) > 2 and name.startswith("on") and name[2].isupper():
            return HoleKind.EVENT
        if name == "bindValue":
            return HoleKind.BIND
        return HoleKind.ATTR


def render(component_fn: Callable[[], Fragment]) -> Rendered:
    fragment = component_fn()
    skeleton: Skeleton = parse(fragment.template.strings)
    out = Rendered()
    emitter = _Emitter(component_fn.__globals__, fragment.template.values, out)
    out.body = emitter.nodes(skeleton.root)
    out.css = stylesheet()
    return out
