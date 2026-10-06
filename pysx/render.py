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
from annotationlib import Format
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from inspect import Parameter, signature
from string.templatelib import Template
from typing import TYPE_CHECKING, cast

from .composition import Children, Component, identity_for, namespace_for
from .dom import DomController, DomError, DomRef, dom_context
from .elements import VOID, ElementTag
from .events import EventHandler
from .forms import Binding, make_binding
from .lifecycle import Scopes
from .parser import Conditional, Element, Hole, HoleKind, Node, Skeleton, attr_kind, parse
from .reactive import Readable, Signal
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

if TYPE_CHECKING:
    from .wire import Op


@dataclass(frozen=True)
class Fragment:
    template: Template
    namespace: Mapping[str, object] | None = None
    root_classes: tuple[object, ...] = ()
    themes: Themes | None = None
    rules: tuple[tuple[str, str], ...] = ()


type Used = Callable[..., object] | ElementTag | StyledTag


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


def html(
    template: Template,
    *,
    use: tuple[Used, ...] = (),
    namespace: Mapping[str, object] | None = None,
    themes: Themes | None = None,
) -> Fragment:
    if not isinstance(template, Template):  # pyright: ignore[reportUnnecessaryIsInstance]

        raise TypeError('html() takes a t-string; did you write f""" instead of t"""?')
    bindings = _used(use)

    if namespace is not None:
        bindings.update(namespace)

    return Fragment(template, bindings or None, themes=themes)


def _template_skeleton(template: Template) -> Skeleton:
    skeleton = parse(template.strings)

    for index, _kind, name in skeleton.holes:
        if name is None or normalize_attr(name) not in {"css", "stylevars", "cssvars"}:

            continue
        interpolation = template.interpolations[index]

        if interpolation.conversion is not None or interpolation.format_spec:

            raise ValueError("CSS attribute interpolation metadata is unsupported")

    return skeleton


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
    scopes: Scopes = field(default_factory=Scopes)
    styles: StyleRegistry = field(default_factory=StyleRegistry)
    dom: DomController = field(default_factory=DomController)
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
    event_handlers: dict[str, tuple[EventHandler, str]] = field(
        default_factory=dict[str, tuple[EventHandler, str]]
    )

    def dispose(self) -> None:
        """Release standalone render resources; sessions call this on disconnect."""
        errors: list[Exception] = []

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
    ) -> None:
        self.ns = ns
        self.values = values
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

    def _resolve(self, tag: str | Hole) -> tuple[str, list[object]]:
        found = self.values[tag.index] if isinstance(tag, Hole) else self.ns.get(tag)

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
        self, nodes: list[Node], *, static: bool = False, root_classes: tuple[object, ...] = ()
    ) -> str:
        parts: list[str] = []

        for node in nodes:
            if isinstance(node, str):
                parts.append(_htmlmod.escape(node))
            elif isinstance(node, Hole):
                parts.append(self.hole(node, static=static, root_classes=root_classes))
            elif isinstance(node, Conditional):
                parts.append(self.conditional(node, static=static, root_classes=root_classes))
            else:
                parts.append(self.element(node, static=static, root_classes=root_classes))

        return "".join(parts)

    def hole(self, hole: Hole, *, static: bool, root_classes: tuple[object, ...] = ()) -> str:
        value = self.values[hole.index]

        if isinstance(value, Children):
            sub = _Emitter(
                dict(value.namespace),
                value.values,
                self.out,
                prefix=f"{self.prefix}f{hole.index}:",
                scope_prefix=f"{self.scope_prefix}f{hole.index}:",
            )

            return sub.nodes(list(value.nodes), static=static, root_classes=root_classes)

        if isinstance(value, (Fragment, Template)):
            fragment = value if isinstance(value, Fragment) else Fragment(value)
            namespace = self.ns | dict(fragment.namespace or {})
            owner = f"{self.prefix}f{hole.index}:"

            for name, body in fragment.rules:
                self.out.styles.add(owner, name, body)
            sub = _Emitter(
                namespace,
                fragment.template.values,
                self.out,
                prefix=f"{self.prefix}f{hole.index}:",
                scope_prefix=f"{self.scope_prefix}f{hole.index}:",
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
        text = str(_read(value))

        if isinstance(value, Signal) and not static:
            self.out.watchers.append(TextWatcher(slot, cast("Readable[object]", value), text))
        escaped_slot = _htmlmod.escape(slot, quote=True)

        return f'<pysx-slot id="{escaped_slot}">{_htmlmod.escape(text)}</pysx-slot>'

    def conditional(
        self, node: Conditional, *, static: bool, root_classes: tuple[object, ...] = ()
    ) -> str:
        value = self.values[node.hole.index]
        slot = f"{self.prefix}{node.hole.index}"

        prefix = f"{slot}:branch:"
        scope_base = f"{self.scope_prefix}{node.hole.index}:branch:"

        def branch(flag: bool) -> tuple[str, list[Watcher]]:
            scope_prefix = f"{scope_base}{int(flag)}:"
            self.out.scopes.release(f"{scope_base}{int(not flag)}:")
            self.out.dom.revoke(prefix)
            self.out.styles.release(prefix)

            for hid in tuple(self.out.handlers):
                if self.out.handler_owners.get(hid, "").startswith(prefix):
                    self.out.handlers.pop(hid, None)
                    self.out.bindings.pop(hid, None)
                    self.out.bind_elements.pop(hid, None)
                    self.out.handler_owners.pop(hid, None)
                    self.out.event_handlers.pop(hid, None)
            start = len(self.out.watchers)
            sub = _Emitter(
                self.ns,
                self.values,
                self.out,
                prefix=prefix,
                handler_prefix="" if not self.handler_prefix else prefix,
                scope_prefix=scope_prefix,
            )
            with self.out.scopes.reconcile(scope_prefix):
                markup = sub.nodes(
                    node.then if flag else node.otherwise, static=static, root_classes=root_classes
                )
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

        def render_items(items: Iterable[object]) -> tuple[list[str], dict[str, str]]:
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
                for item in items:
                    key = str(spec.key(item))

                    if key in markup:

                        raise ValueError(f"duplicate list key {key!r}")
                    order.append(key)
                    markup[key] = self.item(
                        spec, item, slot, key, scope_slot=scope_slot, root_classes=root_classes
                    )

            return order, markup

        order, markup = render_items(spec.source())

        if not static:
            self.out.watchers.append(
                ListWatcher(slot, spec.source, spec, render_items, order, markup)
            )
        inner = "".join(markup[k] for k in order)

        return f'<pysx-list id="{_htmlmod.escape(slot, quote=True)}">{inner}</pysx-list>'

    def item(
        self,
        spec: Each[object],
        item: object,
        slot: str,
        key: str,
        *,
        scope_slot: str,
        root_classes: tuple[object, ...] = (),
    ) -> str:
        token = dom_context.set(self.out.dom)
        owner_token = style_owner.set(f"{slot}:{key}:")

        try:
            with self.out.scopes.enter(f"{scope_slot}:k{len(key)}:{key}:", identity_for(spec.item)):
                fragment = spec.item(item)
        finally:
            dom_context.reset(token)
            style_owner.reset(owner_token)
        skeleton = _template_skeleton(fragment.template)
        prefix = f"{slot}:{key}:"

        for name, body in fragment.rules:
            self.out.styles.add(prefix, name, body)
        sub = _Emitter(
            self.ns | dict(fragment.namespace or {}),
            fragment.template.values,
            self.out,
            prefix=prefix,
            scope_prefix=f"{scope_slot}:k{len(key)}:{key}:",
        )
        markup = sub.nodes(
            skeleton.root, static=True, root_classes=(*fragment.root_classes, *root_classes)
        )

        # Tag the item root so the client can reorder by key.

        return markup.replace(">", f' data-pysx-key="{_htmlmod.escape(key, quote=True)}">', 1)

    def call_component(
        self, fn: Component, el: Element, *, static: bool, root_classes: tuple[object, ...]
    ) -> str:
        props = {
            name: self.values[value.index] if isinstance(value, Hole) else value
            for name, value in el.attrs
        }

        if el.children:
            props["children"] = Children(tuple(el.children), self.values, dict(self.ns))
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
            variant()
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
        namespace = namespace_for(fn) | dict(fragment.namespace or {})

        for name, body in fragment.rules:
            self.out.styles.add(prefix, name, body)
        sub = _Emitter(
            namespace,
            fragment.template.values,
            self.out,
            prefix=prefix,
            scope_prefix=scope_prefix,
        )

        return sub.nodes(
            _template_skeleton(fragment.template).root,
            static=static,
            root_classes=(*fragment.root_classes, *root_classes, *((variant,) if variant else ())),
        )

    def element(self, el: Element, *, static: bool, root_classes: tuple[object, ...] = ()) -> str:
        found = self.values[el.tag.index] if isinstance(el.tag, Hole) else self.ns.get(el.tag)

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
            source = self.values[variant.index] if isinstance(variant, Hole) else variant
            classes.append(VariantClass(found.variants, source))

        if tag == "fragment":
            if el.attrs:

                raise ValueError("fragment does not accept DOM attributes")

            return self.nodes(el.children, static=static, root_classes=(*scoped, *root_classes))
        self.out.styles.apply(_class_text(value) for value in classes if _class_text(value))

        if isinstance(found, StyledTag):
            self.out.styles.apply(cls for _name, cls, _body in found.variants)
        attrs: list[str] = []
        typed_types: list[str] = []
        element_id: str | None = None
        initial_value: str | None = None
        raw_attributes = {
            name: self.values[value.index] if isinstance(value, Hole) else value
            for name, value in el.attrs
        }

        if sum(attr_kind(name) is HoleKind.BIND for name, _value in el.attrs) > 1:

            raise TypeError("a form control accepts exactly one binding")
        control_type = str(_read(raw_attributes.get("type", "text")))
        multiple = _attr_text(raw_attributes.get("multiple"), "multiple") is not None
        normalized = {normalize_attr(name): value for name, value in raw_attributes.items()}
        variables = normalized.get("stylevars", normalized.get("cssvars"))

        if "css" in normalized:
            source = normalized["css"]
            element_id = self._next_element_id()
            owner = f"{self.prefix}css:{element_id}"
            self.out.styles.replace(owner, css_text(source))
            classes.append(CssClass(source))

            if isinstance(source, Signal) and not static:
                self.out.watchers.append(
                    CssWatcher(self.out.styles, owner, cast("Signal[object]", source))
                )

        if variables is not None:
            element_id = element_id or self._next_element_id()
            style_watcher = InlineStyleWatcher(element_id, normalized.get("style"), variables)
            style_watcher.last = style_watcher.value()

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

            raw = self.values[value.index]

            if name == "ref":
                if not isinstance(raw, DomRef):

                    raise TypeError("ref requires a DomRef")
                token = self.out.dom.mount(raw, self.prefix)
                attrs.append(f' data-pysx-ref="{token}"')

                if raw.imperative:
                    if el.children:

                        raise DomError("imperative zones must have no reactive children")
                    attrs.append(' data-pysx-imperative="true"')

                continue

            if kind is HoleKind.EVENT:
                hid = self._handler_id(value.index)
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
            watcher.last = watcher.merged()
            self.out.watchers.append(watcher)
            class_attr = watcher.last
        else:
            class_attr = _merge_classes(_class_text(value) for value in classes)

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
        content = (
            _htmlmod.escape(initial_value)
            if tag == "textarea" and initial_value is not None
            else self.nodes(el.children, static=static)
        )

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
            namespace_for(component_fn) | dict(namespace or {}) | dict(fragment.namespace or {}),
            fragment.template.values,
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
