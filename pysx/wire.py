"""Static contracts for the current v2 wire protocol."""

from typing import Literal, NotRequired, TypedDict

type JSONValue = bool | int | float | str | list[JSONValue] | dict[str, JSONValue] | None


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


type Op = TextOp | HtmlOp | AttrOp | PropertyOp | ListOp | CssOp


class InitMessage(TypedDict):
    t: Literal["init"]
    html: str
    css: str
    routing: NotRequired[bool]


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
    | PatchMessage
    | DomCommandMessage
    | MountMessage
    | NavigationMessage
    | RoutingMessage
)
