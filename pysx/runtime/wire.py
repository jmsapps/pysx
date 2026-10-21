"""Wire contracts and bounded runtime validation for v3 and compatible v2 peers."""

import json
import math
from typing import Literal, NotRequired, TypedDict, cast

from .forms import PayloadError, form_edits

type JSONValue = bool | int | float | str | list[JSONValue] | dict[str, JSONValue] | None

PROTOCOL_VERSION = 3
MAX_FRAME_BYTES = 65536


def _json(value: object, depth: int = 0) -> bool:
    if depth > 16:
        return False

    if value is None or type(value) in (bool, int, str):
        return True

    if isinstance(value, float):
        return math.isfinite(value)

    if isinstance(value, list):
        entries = cast("list[object]", value)

        return len(entries) <= 4096 and all(_json(v, depth + 1) for v in entries)

    if isinstance(value, dict):
        fields = cast("dict[object, object]", value)

        return len(fields) <= 4096 and all(
            isinstance(k, str) and _json(v, depth + 1) for k, v in fields.items()
        )

    return False


def _integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= 2**53 - 1


def _strings(value: object, limit: int = 1024) -> bool:
    if not isinstance(value, list):
        return False
    entries = cast("list[object]", value)

    return len(entries) <= limit and all(isinstance(v, str) and 0 < len(v) <= 512 for v in entries)


def parse_message(
    raw: str | bytes, *, modern: bool = False, limit: int = MAX_FRAME_BYTES
) -> dict[str, object]:
    """Reject unsupported shapes before ownership checks or any user code runs."""

    if len(raw.encode() if isinstance(raw, str) else raw) > limit:
        raise PayloadError("frame size limit")

    try:
        value: object = json.loads(raw)
    except (ValueError, TypeError, RecursionError) as error:
        raise PayloadError("invalid JSON") from error

    if not isinstance(value, dict) or not _json(cast("object", value)):
        raise PayloadError("expected bounded JSON object")
    message = cast("dict[str, object]", value)
    kind = message.get("t")
    fields: dict[str, set[str]] = {
        "hello": {"version", "edits", "resume", "epoch", "state_rev", "recovery"},
        "adopt": {"edits"},
        "event": {"h", "v", "rev", "after", "edits", "event", "nav_rev", "navigation"},
        "mounted": {"ids"},
        "location": {"url", "rev"},
        "dom_reply": {"version", "id", "value", "error", "revoked"},
        "repair": set(),
    }

    if not isinstance(kind, str) or kind not in fields:
        raise PayloadError("unknown message")

    if modern and kind in ("hello", "adopt"):
        raise PayloadError("unexpected handshake message")
    allowed = fields[kind] | {"t"}

    if modern and kind != "hello":
        allowed |= {"version", "epoch", "state_rev", "seq"}

        if message.get("version") != PROTOCOL_VERSION or type(message.get("version")) is not int:
            raise PayloadError("unsupported protocol version")

        if not isinstance(message.get("epoch"), str) or not _integer(message.get("state_rev")):
            raise PayloadError("invalid envelope")

        if kind in ("event", "location", "mounted") and not _integer(message.get("seq")):
            raise PayloadError("invalid event sequence")

    if set(message) - allowed:
        raise PayloadError("unknown message fields")

    if kind == "hello":
        if type(message.get("version")) is not int or message.get("version") != PROTOCOL_VERSION:
            raise PayloadError("unsupported protocol version")

        for name in ("resume", "epoch", "recovery"):
            item = message.get(name)

            if name in message and (not isinstance(item, str) or len(item) > 512):
                raise PayloadError("invalid hello")

        if "state_rev" in message and not _integer(message["state_rev"]):
            raise PayloadError("invalid revision")

        if "resume" in message and (
            not message["resume"] or not message.get("epoch") or "state_rev" not in message
        ):
            raise PayloadError("incomplete resume hello")

    if kind in ("hello", "adopt", "event"):
        if ("edits" in message and not isinstance(message["edits"], list)) or (
            kind == "adopt" and "edits" not in message
        ):
            raise PayloadError("invalid edit collection")
        edits = form_edits(message.get("edits"))

        if len(edits) > 1024 or len({hid for hid, _, _ in edits}) != len(edits):
            raise PayloadError("invalid edit collection")

        for entry in cast("list[dict[str, object]]", message.get("edits") or []):
            handler = entry["h"]

            if (
                set(entry) - {"h", "v", "rev"}
                or "v" not in entry
                or not isinstance(handler, str)
                or not 0 < len(handler) <= 512
                or ("rev" in entry and not _integer(entry["rev"]))
            ):
                raise PayloadError("invalid edit shape")

    if kind == "event":
        if (
            not isinstance(message.get("h"), str)
            or len(cast("str", message["h"])) > 512
            or (not message["h"] and not message.get("edits"))
        ):
            raise PayloadError("invalid handler")

        if "after" in message and not isinstance(message["after"], str):
            raise PayloadError("invalid chained handler")

        if "navigation" in message and type(message["navigation"]) is not bool:
            raise PayloadError("invalid navigation flag")

        if "event" in message and not isinstance(message["event"], dict):
            raise PayloadError("invalid event snapshot")

    if kind in ("event", "location"):
        for name in ("rev", "nav_rev"):
            if name in message and not _integer(message[name]):
                raise PayloadError("invalid revision")

    if kind == "location" and (
        not isinstance(message.get("url"), str) or not _integer(message.get("rev"))
    ):
        raise PayloadError("invalid location")

    if kind == "mounted" and not _strings(message.get("ids")):
        raise PayloadError("invalid mount acknowledgements")

    if kind == "dom_reply" and (
        message.get("version") != (PROTOCOL_VERSION if modern else 1)
        or type(message.get("version")) is not int
        or not isinstance(message.get("id"), str)
        or ("error" in message and not isinstance(message["error"], str))
        or ("revoked" in message and not _strings(message["revoked"]))
        or ("value" in message) == ("error" in message)
    ):
        raise PayloadError("invalid command reply")

    return message


class TextOp(TypedDict):
    op: Literal["text"]
    id: str
    v: str


class HtmlOp(TypedDict):
    op: Literal["html"]
    id: str
    v: str


class AttrOp(TypedDict):
    op: Literal["attr"]
    id: str
    name: str
    v: str | None
    rev: NotRequired[int]


class PropertyOp(TypedDict):
    op: Literal["prop"]
    id: str
    name: Literal["selectedValues"]
    v: list[str]
    rev: NotRequired[int]


class ListOp(TypedDict):
    op: Literal["list"]
    id: str
    keys: list[str]
    html: dict[str, str]


class CssOp(TypedDict):
    op: Literal["css"]
    v: str


class ChildrenOp(TypedDict):
    op: Literal["children"]
    id: str
    range: bool
    kind: str
    v: str


type Op = TextOp | HtmlOp | AttrOp | PropertyOp | ListOp | CssOp | ChildrenOp


class InitMessage(TypedDict):
    t: Literal["init"]
    html: str
    css: str
    routing: NotRequired[bool]


class AdoptedMessage(TypedDict):
    t: Literal["adopted"]
    routing: bool


class NavigationMessage(TypedDict):
    t: Literal["navigation"]
    url: str
    rev: int
    mode: Literal["push", "replace", "observe", "external"]


class RoutingMessage(TypedDict):
    t: Literal["routing"]
    on: bool


class PatchMessage(TypedDict):
    t: Literal["patch"]
    ops: list[Op]


class ErrorDetail(TypedDict):
    id: str
    owner: str | None
    phase: Literal["render", "handler", "computed", "effect", "cleanup", "mount", "async"]
    message: str


class ResultMessage(TypedDict):
    t: Literal["result"]
    h: str
    status: Literal["success", "error", "invalid", "stale"]
    ops: list[Op]
    error: NotRequired[ErrorDetail]


class MountMessage(TypedDict):
    t: Literal["mount"]
    ids: list[str]


class MountedMessage(TypedDict):
    t: Literal["mounted"]
    ids: list[str]


class EventMessage(TypedDict):
    t: Literal["event"]
    h: str
    v: NotRequired[JSONValue]
    rev: NotRequired[int]
    after: NotRequired[str]
    edits: NotRequired[list[BindingEdit]]
    event: NotRequired[dict[str, JSONValue]]
    nav_rev: NotRequired[int]
    navigation: NotRequired[bool]


class LocationMessage(TypedDict):
    t: Literal["location"]
    url: str
    rev: int


class BindingEdit(TypedDict):
    h: str
    v: str | bool | list[str]
    rev: int


class Envelope(TypedDict):
    version: Literal[3]
    epoch: str
    state_rev: int


class HelloMessage(TypedDict):
    t: Literal["hello"]
    version: Literal[3]
    edits: NotRequired[list[BindingEdit]]
    resume: NotRequired[str]
    epoch: NotRequired[str]
    state_rev: NotRequired[int]


class AdoptionMessage(TypedDict):
    t: Literal["adopt"]
    edits: list[BindingEdit]


class AckMessage(Envelope):
    t: Literal["ack"]
    base_rev: int
    seq: int
    status: Literal["success", "duplicate", "gap", "epoch", "revision"]
    expected: int


class RepairMessage(Envelope):
    t: Literal["repair"]
    base_rev: int
    html: str
    css: str
    expected: int


class DomCommandMessage(TypedDict):
    t: Literal["dom"]
    version: Literal[1]
    id: str
    target: str
    root: str
    op: str
    args: dict[str, JSONValue]


class DomReplyMessage(TypedDict):
    t: Literal["dom_reply"]
    version: Literal[1]
    id: str
    value: NotRequired[JSONValue]
    error: NotRequired[str]
    revoked: NotRequired[list[str]]


type ServerMessage = (
    InitMessage
    | AdoptedMessage
    | PatchMessage
    | DomCommandMessage
    | MountMessage
    | NavigationMessage
    | RoutingMessage
    | ResultMessage
    | AckMessage
    | RepairMessage
)

type ClientMessage = (
    HelloMessage
    | AdoptionMessage
    | EventMessage
    | LocationMessage
    | MountedMessage
    | DomReplyMessage
)
