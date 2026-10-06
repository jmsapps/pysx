"""Native schema coverage and typed constructor/runtime agreement."""

import dataclasses
import json
from pathlib import Path
from typing import get_type_hints

import pytest

from pysx import Signal, elements, native, render, signal, styled
from pysx.native_support import element
from pysx.parser import Element, parse
from pysx.schema import (
    BASELINE_TAGS,
    EVENT_NAMES,
    PSEUDO_ATTRS,
    VOID,
    AttrFamily,
    allowed_attr,
    family_of,
    normalize_attr,
    tag_info,
)
from scripts.generate_native import CLIENT, generate_client, spelling

BASELINE = json.loads((Path(__file__).parent / "fixtures" / "html_schema.json").read_text())


def ignore_event(_event: object) -> None:
    pass


@pytest.mark.parametrize("tag", BASELINE["DSL_TAGS"])
@pytest.mark.parametrize("reactive", [False, True])
def test_schema_native_rows(tag: str, reactive: bool) -> None:
    marker = getattr(elements, "del_" if tag == "del" else tag)
    assert str(marker) == tag
    info = tag_info(tag)
    assert info is not None
    assert info.name == tag
    assert info.void == (tag in VOID)
    attributes = BASELINE["TAG_ATTRS"].get(tag, [])
    assert set(attributes) <= info.attributes
    hints = get_type_hints(getattr(native, tag.capitalize() + "Attrs"))
    values: dict[str, object] = {}
    sources: dict[str, Signal[object]] = {}

    for attr in attributes:
        assert allowed_attr(tag, attr)
        assert spelling(attr) in hints
        values[attr] = (
            ignore_event
            if attr.startswith("on")
            else True
            if family_of(attr) is AttrFamily.BOOLEAN
            else "2"
        )

        if reactive and not attr.startswith("on"):
            sources[attr] = signal(values[attr])
            values[attr] = sources[attr]
    values["id"] = "native-fixture"
    values["data-case"] = tag

    if tag == "fragment":
        fragment = element(tag, ("body",), {})
        assert render(lambda: fragment).body == '<pysx-slot id="0">body</pysx-slot>'
    else:
        fragment = element(tag, (), values)
        rendered = render(lambda: fragment)
        markup = rendered.body
        assert markup.startswith(f"<{tag}")
        assert 'id="native-fixture"' in markup
        assert f'data-case="{tag}"' in markup
        assert markup.endswith(">") if info.void else markup.endswith(f"</{tag}>")

        for source in sources.values():
            source.set(False if isinstance(source(), bool) else "3")
        ops = [op for watcher in rendered.watchers for op in watcher.refresh()]
        assert len(ops) == len(sources)


def test_schema_native_queries_and_escapes() -> None:
    assert len(BASELINE_TAGS) == 113
    assert len(VOID) == 14
    assert set("".join(BASELINE["EVENT_NAMES"]).split()) == EVENT_NAMES
    assert set("".join(BASELINE["PSEUDO_ATTRS"]).split()) == PSEUDO_ATTRS

    for event in EVENT_NAMES:
        assert allowed_attr("div", "on" + event.capitalize())
        assert family_of("on_" + event) is AttrFamily.EVENT

    for pseudo in PSEUDO_ATTRS:
        assert allowed_attr("div", pseudo)
    assert not allowed_attr("div", "href")
    assert allowed_attr("a", "href")
    assert allowed_attr("div", "dataRevealDelay")
    assert allowed_attr("div", "ariaActiveDescendant")
    assert normalize_attr("dataRevealDelay") == "data-reveal-delay"
    assert normalize_attr("ariaActiveDescendant") == "aria-activedescendant"
    assert normalize_attr("max_length") == "maxlength"
    assert normalize_attr("className") == "class"
    assert normalize_attr("htmlFor") == "for"
    assert tag_info("svg").namespace == "svg"  # type: ignore[union-attr]
    assert tag_info("math").namespace == "math"  # type: ignore[union-attr]
    assert tag_info("my-widget") is None
    fragment = native.custom_element("my-widget", "Hello", custom_attrs={"private": signal("a")})
    rendered = render(lambda: fragment)
    assert '<my-widget private="a"' in rendered.body
    assert len(rendered.watchers) == 1

    with pytest.raises(ValueError, match="runtime"):
        native.custom_element("my-widget", custom_attrs={"onclick": "evil()"})

    with pytest.raises(ValueError, match="void"):
        native.Input("child")


def test_schema_native_composition_and_frozen_markers() -> None:
    count = signal(1)
    fragment = native.Div(native.Span(count), native.A("link", href="/"), id="typed")
    rendered = render(lambda: fragment)
    assert '<div id="typed"><span>' in rendered.body
    count.set(2)
    op = rendered.watchers[0].refresh()[0]
    assert op["op"] == "text"
    assert op["v"] == "2"

    field_name = "name"

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(elements.div, field_name, "other")
    assert styled(elements.div, t"color: red").tag == "div"


@pytest.mark.parametrize("tag", sorted(VOID))
def test_serialization_live_voids(tag: str) -> None:
    fragment = element(tag, (), {"title": '<&"quoted"', "hidden": False, "aria-hidden": False})
    markup = render(lambda: fragment).body
    assert "</" not in markup
    assert 'title="&lt;&amp;&quot;quoted&quot;"' in markup
    assert 'aria-hidden="false"' in markup
    assert " hidden" not in markup


def test_serialization_live_namespace_metadata() -> None:
    nodes = parse(
        (
            '\nsvg(viewBox="0 0 10 10"):\n  circle():\n  foreignObject():\n'
            '    div():\nmath():\n  mi(): "x"\n',
        )
    ).root
    svg = nodes[0]
    assert isinstance(svg, Element)
    assert svg.namespace == "svg"
    circle = svg.children[0]
    assert isinstance(circle, Element)
    assert circle.namespace == "svg"
    foreign = svg.children[1]
    assert isinstance(foreign, Element)
    div_node = foreign.children[0]
    assert isinstance(div_node, Element)
    assert div_node.namespace == "html"


def test_client_delegates_every_event_the_renderer_can_emit() -> None:
    source = CLIENT.read_text()
    assert generate_client(source) == source, (
        "client.js event delegation is stale; run scripts/generate_native.py"
    )
