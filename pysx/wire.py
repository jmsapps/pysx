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


class ListOp(TypedDict):
    op: Literal["list"]
    id: str
    keys: list[str]
    html: dict[str, str]


type Op = TextOp | HtmlOp | AttrOp | ListOp


class InitMessage(TypedDict):
    t: Literal["init"]
    html: str
    css: str


class PatchMessage(TypedDict):
    t: Literal["patch"]
    ops: list[Op]


class EventMessage(TypedDict):
    t: Literal["event"]
    h: str
    v: NotRequired[JSONValue]


type ServerMessage = InitMessage | PatchMessage
