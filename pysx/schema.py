"""Shared native HTML metadata and advisory schema queries."""

from __future__ import annotations

import re
from collections.abc import Mapping  # noqa: TC003 - public runtime metadata annotations
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

BASELINE_TAGS = (
    "a",
    "abbr",
    "address",
    "area",
    "article",
    "aside",
    "audio",
    "b",
    "base",
    "bdi",
    "bdo",
    "blockquote",
    "body",
    "br",
    "button",
    "canvas",
    "caption",
    "cite",
    "code",
    "col",
    "colgroup",
    "data",
    "datalist",
    "dd",
    "del",
    "details",
    "dfn",
    "dialog",
    "d",
    "dl",
    "dt",
    "em",
    "embed",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "fragment",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "head",
    "header",
    "hr",
    "html",
    "i",
    "iframe",
    "img",
    "input",
    "ins",
    "kbd",
    "label",
    "legend",
    "li",
    "link",
    "main",
    "map",
    "mark",
    "menu",
    "meta",
    "meter",
    "nav",
    "noscript",
    "obj",
    "ol",
    "optgroup",
    "option",
    "output",
    "p",
    "param",
    "picture",
    "pre",
    "progress",
    "q",
    "rp",
    "rt",
    "ruby",
    "s",
    "samp",
    "script",
    "section",
    "select",
    "slot",
    "small",
    "source",
    "span",
    "strong",
    "style",
    "sub",
    "summary",
    "sup",
    "svg",
    "table",
    "tbody",
    "td",
    "tmpl",
    "textarea",
    "tfoot",
    "th",
    "thead",
    "time",
    "title",
    "tr",
    "track",
    "u",
    "ul",
    "v",
    "video",
    "wbr",
)
ALIASES: Mapping[str, str] = MappingProxyType(
    {"d": "div", "obj": "object", "tmpl": "template", "v": "var"}
)
GLOBAL_ATTRS = frozenset(
    [
        "accesskey",
        "autocapitalize",
        "autofocus",
        "class",
        "contenteditable",
        "dir",
        "draggable",
        "enterkeyhint",
        "hidden",
        "id",
        "inert",
        "inputmode",
        "is",
        "itemid",
        "itemprop",
        "itemref",
        "itemscope",
        "itemtype",
        "lang",
        "nonce",
        "part",
        "popover",
        "role",
        "slot",
        "spellcheck",
        "style",
        "tabindex",
        "title",
        "translate",
    ]
)
PSEUDO_ATTRS = frozenset(["key", "css", "stylevars", "cssvars", "customattrs"])
RUNTIME_ATTRS = frozenset(["ref"])
EVENT_NAMES = frozenset(
    [
        "abort",
        "animationend",
        "animationiteration",
        "animationstart",
        "auxclick",
        "beforeinput",
        "blur",
        "cancel",
        "canplay",
        "canplaythrough",
        "change",
        "click",
        "close",
        "compositionend",
        "compositionstart",
        "compositionupdate",
        "contextmenu",
        "copy",
        "cuechange",
        "cut",
        "dblclick",
        "drag",
        "dragend",
        "dragenter",
        "dragleave",
        "dragover",
        "dragstart",
        "drop",
        "durationchange",
        "emptied",
        "ended",
        "error",
        "focus",
        "focusin",
        "focusout",
        "formdata",
        "fullscreenchange",
        "gotpointercapture",
        "input",
        "invalid",
        "keydown",
        "keypress",
        "keyup",
        "load",
        "loadeddata",
        "loadedmetadata",
        "loadstart",
        "lostpointercapture",
        "mousedown",
        "mouseenter",
        "mouseleave",
        "mousemove",
        "mouseout",
        "mouseover",
        "mouseup",
        "mousewheel",
        "paste",
        "pause",
        "play",
        "playing",
        "pointercancel",
        "pointerdown",
        "pointerenter",
        "pointerleave",
        "pointermove",
        "pointerout",
        "pointerover",
        "pointerup",
        "progress",
        "ratechange",
        "reset",
        "resize",
        "scroll",
        "scrollend",
        "securitypolicyviolation",
        "seeked",
        "seeking",
        "select",
        "selectionchange",
        "selectstart",
        "show",
        "slotchange",
        "stalled",
        "submit",
        "suspend",
        "timeupdate",
        "toggle",
        "touchcancel",
        "touchend",
        "touchmove",
        "touchstart",
        "transitionend",
        "volumechange",
        "waiting",
        "wheel",
    ]
)
VOID = frozenset(
    [
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    ]
)
BOOLEAN_ATTRS = frozenset(
    [
        "allowfullscreen",
        "async",
        "autofocus",
        "autoplay",
        "checked",
        "controls",
        "default",
        "defer",
        "disabled",
        "formnovalidate",
        "hidden",
        "inert",
        "ismap",
        "itemscope",
        "loop",
        "multiple",
        "muted",
        "nomodule",
        "novalidate",
        "open",
        "playsinline",
        "readonly",
        "required",
        "reversed",
        "selected",
    ]
)
NUMBER_ATTRS = frozenset(
    [
        "cols",
        "colspan",
        "height",
        "high",
        "low",
        "max",
        "maxlength",
        "min",
        "minlength",
        "optimum",
        "rows",
        "rowspan",
        "size",
        "span",
        "start",
        "step",
        "tabindex",
        "value",
        "width",
    ]
)
TAG_ATTRS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "a": frozenset(
            ["download", "href", "hreflang", "ping", "referrerpolicy", "rel", "target", "type"]
        ),
        "area": frozenset(
            [
                "alt",
                "coords",
                "download",
                "href",
                "hreflang",
                "ping",
                "referrerpolicy",
                "rel",
                "shape",
                "target",
            ]
        ),
        "audio": frozenset(
            ["autoplay", "controls", "crossorigin", "loop", "muted", "preload", "src"]
        ),
        "base": frozenset(["href", "target"]),
        "blockquote": frozenset(["cite"]),
        "body": frozenset(
            [
                "onafterprint",
                "onbeforeprint",
                "onbeforeunload",
                "onhashchange",
                "onload",
                "onpopstate",
                "onunload",
            ]
        ),
        "button": frozenset(
            [
                "disabled",
                "form",
                "formaction",
                "formenctype",
                "formmethod",
                "formnovalidate",
                "formtarget",
                "name",
                "popovertarget",
                "popovertargetaction",
                "type",
                "value",
            ]
        ),
        "canvas": frozenset(["height", "width"]),
        "col": frozenset(["span"]),
        "colgroup": frozenset(["span"]),
        "data": frozenset(["value"]),
        "del": frozenset(["cite", "datetime"]),
        "details": frozenset(["name", "open"]),
        "dialog": frozenset(["open"]),
        "embed": frozenset(["height", "src", "type", "width"]),
        "fieldset": frozenset(["disabled", "form", "name"]),
        "form": frozenset(
            [
                "accept-charset",
                "action",
                "autocomplete",
                "enctype",
                "method",
                "name",
                "novalidate",
                "rel",
                "target",
            ]
        ),
        "html": frozenset(["manifest"]),
        "iframe": frozenset(
            [
                "allow",
                "allowfullscreen",
                "height",
                "loading",
                "name",
                "referrerpolicy",
                "sandbox",
                "src",
                "srcdoc",
                "width",
            ]
        ),
        "img": frozenset(
            [
                "alt",
                "crossorigin",
                "decoding",
                "fetchpriority",
                "height",
                "ismap",
                "loading",
                "referrerpolicy",
                "sizes",
                "src",
                "srcset",
                "usemap",
                "width",
            ]
        ),
        "input": frozenset(
            [
                "accept",
                "alt",
                "autocomplete",
                "capture",
                "checked",
                "dirname",
                "disabled",
                "form",
                "formaction",
                "formenctype",
                "formmethod",
                "formnovalidate",
                "formtarget",
                "height",
                "list",
                "max",
                "maxlength",
                "min",
                "minlength",
                "multiple",
                "name",
                "pattern",
                "placeholder",
                "popovertarget",
                "popovertargetaction",
                "readonly",
                "required",
                "size",
                "src",
                "step",
                "type",
                "value",
                "width",
            ]
        ),
        "ins": frozenset(["cite", "datetime"]),
        "label": frozenset(["for", "form"]),
        "li": frozenset(["value"]),
        "link": frozenset(
            [
                "as",
                "color",
                "crossorigin",
                "disabled",
                "fetchpriority",
                "href",
                "hreflang",
                "imagesizes",
                "imagesrcset",
                "integrity",
                "media",
                "referrerpolicy",
                "rel",
                "sizes",
                "type",
            ]
        ),
        "map": frozenset(["name"]),
        "meta": frozenset(["charset", "content", "http-equiv", "media", "name"]),
        "meter": frozenset(["form", "high", "low", "max", "min", "optimum", "value"]),
        "object": frozenset(["data", "form", "height", "name", "type", "usemap", "width"]),
        "ol": frozenset(["reversed", "start", "type"]),
        "optgroup": frozenset(["disabled", "label"]),
        "option": frozenset(["disabled", "label", "selected", "value"]),
        "output": frozenset(["for", "form", "name"]),
        "param": frozenset(["name", "value"]),
        "progress": frozenset(["max", "value"]),
        "q": frozenset(["cite"]),
        "script": frozenset(
            [
                "async",
                "crossorigin",
                "defer",
                "fetchpriority",
                "integrity",
                "nomodule",
                "referrerpolicy",
                "src",
                "type",
            ]
        ),
        "select": frozenset(
            ["autocomplete", "disabled", "form", "multiple", "name", "required", "size"]
        ),
        "slot": frozenset(["name"]),
        "source": frozenset(["height", "media", "sizes", "src", "srcset", "type", "width"]),
        "style": frozenset(["media"]),
        "td": frozenset(["colspan", "headers", "rowspan"]),
        "textarea": frozenset(
            [
                "autocomplete",
                "cols",
                "dirname",
                "disabled",
                "form",
                "maxlength",
                "minlength",
                "name",
                "placeholder",
                "readonly",
                "required",
                "rows",
                "wrap",
            ]
        ),
        "th": frozenset(["abbr", "colspan", "headers", "rowspan", "scope"]),
        "time": frozenset(["datetime"]),
        "track": frozenset(["default", "kind", "label", "src", "srclang"]),
        "video": frozenset(
            [
                "autoplay",
                "controls",
                "crossorigin",
                "height",
                "loop",
                "muted",
                "playsinline",
                "poster",
                "preload",
                "src",
                "width",
            ]
        ),
    }
)


class AttrFamily(Enum):
    STRING = "string"
    BOOLEAN = "boolean"
    NUMBER = "number"
    EVENT = "event"


@dataclass(frozen=True)
class TagInfo:
    name: str
    attributes: frozenset[str]
    void: bool = False
    namespace: str = "html"
    fragment: bool = False


def resolve_tag(name: str) -> str:
    return ALIASES.get(name, name)


def normalize_attr(name: str) -> str:
    aliases = {"class_name": "class", "className": "class", "html_for": "for", "htmlFor": "for"}

    if name in aliases:
        return aliases[name]

    if name.startswith(("aria", "data")) and len(name) > 4 and name[4].isupper():
        suffix = name[4:]

        if name.startswith("data"):
            suffix = re.sub(r"(?<!^)([A-Z])", r"-\1", suffix)

        return name[:4] + "-" + suffix.lower()

    if name.startswith(("aria_", "data_")):
        return name.replace("_", "-").lower()

    if name in {"accept_charset", "http_equiv"}:
        return name.replace("_", "-")

    return name.rstrip("_").replace("_", "").lower()


def is_event(name: str) -> bool:
    normalized = normalize_attr(name)

    return normalized.startswith("on") and (
        normalized[2:] in EVENT_NAMES or normalized in TAG_ATTRS["body"]
    )


def family_of(name: str, tag: str | None = None) -> AttrFamily:
    name = normalize_attr(name)

    if is_event(name):
        return AttrFamily.EVENT

    if name in BOOLEAN_ATTRS:
        return AttrFamily.BOOLEAN

    if name in NUMBER_ATTRS and not (
        name == "value" and tag in {"input", "button", "option", "data", "param"}
    ):
        return AttrFamily.NUMBER

    return AttrFamily.STRING


def tag_info(name: str) -> TagInfo | None:
    name = resolve_tag(name)

    if name not in {resolve_tag(tag) for tag in BASELINE_TAGS} | {"math"}:
        return None

    return TagInfo(
        name,
        GLOBAL_ATTRS | TAG_ATTRS.get(name, frozenset()),
        name in VOID,
        name if name in {"svg", "math"} else "html",
        name == "fragment",
    )


def allowed_attr(tag: str, name: str) -> bool:
    name = normalize_attr(name)
    info = tag_info(tag)

    return bool(name) and (
        name.startswith(("data-", "aria-"))
        or is_event(name)
        or name in PSEUDO_ATTRS
        or name in RUNTIME_ATTRS
        or (info is not None and name in info.attributes)
    )
