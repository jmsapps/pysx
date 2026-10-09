"""Owned HTML snapshots and granular diffs for retained render ranges."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import TYPE_CHECKING, cast

from .elements import VOID

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from .wire import Op


@dataclass
class Tree:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict[str, str | None])
    children: list[Tree] = field(default_factory=list["Tree"])
    text: str = ""
    range_kind: str = ""
    identifier: str = ""
    raw: bool = False

    def markup(self) -> str:
        if self.tag == "#text":
            return self.text if self.raw else html.escape(self.text)

        if self.tag == "#comment":
            return f"<!--{self.text}-->"

        if self.tag == "#range":
            token = self.identifier.encode().hex()
            marker = f"pysx:{self.range_kind}:{token}"

            return f"<!--{marker}:start-->{self.inner()}<!--{marker}:end-->"

        if self.tag == "#root":
            return self.inner()
        attrs = "".join(
            f" {name}" if value is None else f' {name}="{html.escape(value, quote=True)}"'
            for name, value in self.attrs.items()
        )
        start = f"<{self.tag}{attrs}>"

        return start if self.tag in VOID else start + self.inner() + f"</{self.tag}>"

    def inner(self) -> str:
        return "".join(child.markup() for child in self.children)


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Tree("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = self.get_starttag_text() or ""
        match = re.match(r"<([\w:-]+)", raw)
        names = re.findall(r"""\s+([^\s=/>]+)(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?""", raw)
        attributes = {
            names[index] if len(names) == len(attrs) else name: value
            for index, (name, value) in enumerate(attrs)
        }
        node = Tree(match[1] if match else tag, attributes)
        self.stack[-1].children.append(node)

        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        if len(self.stack) > 1 and self.stack[-1].tag.lower() == tag:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        parent = self.stack[-1]
        children = parent.children
        raw = parent.tag.lower() in self.CDATA_CONTENT_ELEMENTS

        if children and children[-1].tag == "#text" and children[-1].raw == raw:
            children[-1].text += data
        else:
            children.append(Tree("#text", text=data, raw=raw))

    def handle_comment(self, data: str) -> None:
        self.stack[-1].children.append(Tree("#comment", text=data))


def _ranges(node: Tree) -> None:
    grouped: list[Tree] = []
    stack = [grouped]

    for child in node.children:
        match = (
            re.fullmatch(r"pysx:(slot|list|row):([a-f0-9]+):(start|end)", child.text)
            if child.tag == "#comment"
            else None
        )

        if match and match[3] == "start":
            item = Tree("#range", range_kind=match[1], identifier=bytes.fromhex(match[2]).decode())
            stack[-1].append(item)
            stack.append(item.children)
        elif match and match[3] == "end":
            if len(stack) < 2:
                raise ValueError("unpaired render range")
            stack.pop()
        else:
            _ranges(child)
            stack[-1].append(child)

    if len(stack) != 1:
        raise ValueError("unpaired render range")
    node.children = grouped


def snapshot(markup: str, *, unwrap_row: bool = True) -> Tree:
    parser = _Parser()
    parser.feed(markup)
    parser.close()
    _ranges(parser.root)

    if (
        unwrap_row
        and len(parser.root.children) == 1
        and parser.root.children[0].range_kind == "row"
    ):
        return parser.root.children[0]

    return parser.root


def apply(node: Tree, ops: Sequence[Op]) -> None:
    """Keep retained snapshots equal to the live DOM after owned watcher patches."""
    from .identity import OwnerPath

    def walk(target: Tree, op: Op) -> None:
        if op["op"] == "css":
            return
        identifier = target.identifier or (
            target.attrs.get("id")
            if target.tag == "pysx-slot"
            else target.attrs.get("data-pysx-el")
        )

        if identifier == op["id"]:
            match op["op"]:
                case "text":
                    target.children = [Tree("#text", text=op["v"])]
                case "html" | "children":
                    target.children = snapshot(op["v"], unwrap_row=False).children
                case "attr":
                    if op["v"] is None:
                        target.attrs.pop(op["name"], None)
                    else:
                        target.attrs[op["name"]] = op["v"]
                case "prop":
                    target.attrs["data-pysx-selected"] = json.dumps(op["v"])
                case "list":
                    existing = {child.identifier: child for child in target.children}
                    children: list[Tree] = []

                    for key in op["keys"]:
                        row_id = OwnerPath(op["id"]).row(key).value

                        if key in op["html"]:
                            children.append(snapshot(op["html"][key]))
                        elif row_id in existing:
                            children.append(existing[row_id])
                    target.children = children

            return

        for child in target.children:
            walk(child, op)

    for op in ops:
        walk(node, op)


def diff(before: Tree, after: Tree, owner: str, live: Collection[str] = ()) -> list[Op]:
    """Live nested lists reconcile independently; retained slots/attrs patch directly."""
    ops: list[Op] = []

    def anchored(node: Tree) -> str | None:
        """Only nodes the client can address may own a structural replacement."""

        if node.tag in {"#range", "#root"}:
            return node.identifier or owner

        if node.tag == "pysx-slot":
            return node.attrs.get("id")

        return node.attrs.get("data-pysx-el")

    def structure(node: Tree) -> None:
        ops.append(
            {
                "op": "children",
                "id": anchored(node) or owner,
                "range": node.tag in {"#range", "#root"},
                "kind": node.range_kind or "row",
                "v": node.inner(),
            }
        )

    def walk(old: Tree, new: Tree, anchor: Tree) -> None:
        if new.range_kind == "list" and new.identifier in live:
            return

        if anchored(new):
            anchor = new
        slot = (
            new.identifier
            if new.range_kind == "slot"
            else new.attrs.get("id")
            if new.tag == "pysx-slot"
            else None
        )

        if slot and all(child.tag == "#text" for child in [*old.children, *new.children]):
            previous = "".join(child.text for child in old.children)
            current = "".join(child.text for child in new.children)

            if previous != current:
                ops.append({"op": "text", "id": slot, "v": current})

            return
        identifier = new.attrs.get("data-pysx-el")

        if identifier:
            for name in old.attrs.keys() | new.attrs.keys():
                old_attr, new_attr = old.attrs.get(name), new.attrs.get(name)

                if old_attr != new_attr or (name in old.attrs) != (name in new.attrs):
                    if name == "data-pysx-selected":
                        values: object = json.loads(new_attr or "[]")

                        if not isinstance(values, list) or not all(
                            isinstance(value, str) for value in cast("list[object]", values)
                        ):
                            raise ValueError("invalid selected-value snapshot")
                        ops.append(
                            {
                                "op": "prop",
                                "id": identifier,
                                "name": "selectedValues",
                                "v": cast("list[str]", values),
                            }
                        )

                        continue
                    ops.append(
                        {
                            "op": "attr",
                            "id": identifier,
                            "name": name,
                            "v": (new_attr or "") if name in new.attrs else None,
                        }
                    )

        if len(old.children) != len(new.children) or any(
            left.tag != right.tag
            or left.range_kind != right.range_kind
            or left.identifier != right.identifier
            or left.attrs.get("data-pysx-ref") != right.attrs.get("data-pysx-ref")
            or left.attrs.get("data-pysx-el") != right.attrs.get("data-pysx-el")
            or (left.tag == "pysx-slot" and left.attrs.get("id") != right.attrs.get("id"))
            or (left.tag in {"#text", "#comment"} and left.text != right.text)
            for left, right in zip(old.children, new.children, strict=False)
        ):
            structure(anchor)

            return

        for left, right in zip(old.children, new.children, strict=True):
            walk(left, right, anchor)

    walk(before, after, after)

    return ops
