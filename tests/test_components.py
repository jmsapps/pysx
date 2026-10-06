"""Callable dispatch preserves defining and caller template environments."""

import gc
import weakref
from functools import partial
from string.templatelib import Template
from typing import TYPE_CHECKING

import pytest

from examples.counter import app as imported_app
from pysx import Children, Fragment, html, render
from pysx.composition import namespace_for
from pysx.elements import em, strong
from pysx.parser import Element

if TYPE_CHECKING:
    from collections.abc import Callable


def sample(*, title: str, children: Children | None = None) -> Template:
    return t"\nsection: {title}; {children if children is not None else ''}"


@pytest.mark.parametrize("form", ["function", "alias", "nested", "partial", "instance"])
def test_callable_dispatch_forms(form: str) -> None:
    def nested(*, title: str, children: Children | None = None) -> Fragment:
        return html(sample(title=title, children=children))

    class Factory:
        def __call__(self, *, title: str, children: Children | None = None) -> Template:
            return sample(title=title, children=children)

    candidates = {
        "function": sample,
        "alias": sample,
        "nested": nested,
        "partial": partial(sample, title="default"),
        "instance": Factory(),
    }

    def app() -> Fragment:
        return html(
            t'\nWidget(title="hello"):\n  span: "child"', namespace={"Widget": candidates[form]}
        )

    result = render(app)
    assert "hello" in result.body
    assert "<span>child</span>" in result.body
    assert result.body.startswith("<section>")


def test_callable_dispatch_closure_and_explicit_namespace() -> None:
    def factory() -> Callable[[], Fragment]:
        local = strong

        def child() -> Fragment:
            # A real closure binding is visible without a saved execution frame.
            assert local is strong

            return html(t'\nLocal: "closure"')

        return child

    child = factory()
    assert "local" in namespace_for(child)
    # Python names and DSL names are case sensitive; explicit bindings can alias them.
    result = render(child, namespace={"Local": strong})
    assert result.body == "<strong>closure</strong>"


def test_callable_dispatch_caller_children_structure_and_namespace() -> None:
    seen: list[Children] = []

    def child(*, children: Children) -> Fragment:
        seen.append(children)

        return html(t"\narticle: {children}", namespace={"Caller": em})

    def app() -> Fragment:
        return html(
            t'\nChild:\n  Caller: "owned"', namespace={"Child": child, "Caller": strong}
        )

    result = render(app)
    assert result.body == "<article><strong>owned</strong></article>"
    assert isinstance(seen[0].nodes[0], Element)
    assert seen[0].nodes[0].tag == "Caller"


@pytest.mark.parametrize(
    "body",
    ['\nspan: "one"', '\nspan: "one"\nspan: "two"', '\nfragment:\n  span: "one"\n  span: "two"'],
)
def test_callable_dispatch_root_exposure(body: str) -> None:
    def child() -> Template:
        return Template(body)

    def app() -> Fragment:
        return html(t"\nChild:", namespace={"Child": child})

    result = render(app)
    assert result.body.count("<span>") == body.count("span:")
    assert "fragment" not in result.body


def test_callable_dispatch_invalid_return() -> None:
    def app() -> Fragment:
        return html(t"\nBad:", namespace={"Bad": lambda: "invalid"})

    with pytest.raises(TypeError, match="Template or Fragment"):
        render(app)


def test_callable_dispatch_imported_alias() -> None:
    def app() -> Fragment:
        return html(t'\nImported:', namespace={"Imported": imported_app})

    result = render(app)
    assert "Counter" in result.body
    assert result.handlers


def test_callable_dispatch_no_frame_retention() -> None:
    class Sentinel:
        pass

    sentinel = Sentinel()
    reference = weakref.ref(sentinel)

    def factory(marker: Sentinel) -> Fragment:
        assert isinstance(marker, Sentinel)

        return html(t'\nspan: "done"')

    result = render(partial(factory, sentinel))
    del sentinel
    gc.collect()
    assert reference() is None
    assert result.body == "<span>done</span>"


def test_callable_dispatch_leaves_native_tags_unshadowed() -> None:
    def main() -> Fragment:
        return html(t'\nmain:\n  html: "native"')

    assert render(main).body == "<main><html>native</html></main>"
