"""Callable dispatch preserves defining and caller template environments."""

import gc
import weakref
from functools import partial
from pathlib import Path
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
def test_callable_return_callable_dispatch_forms(form: str) -> None:
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


def test_callable_return_callable_dispatch_closure_and_explicit_namespace() -> None:
    def factory() -> Callable[[], Fragment]:
        local = strong

        def child() -> Fragment:
            # A real closure binding is visible without a saved execution frame.
            assert local is strong

            # Explicit renderer namespaces belong to the raw-fragment compatibility path.
            return Fragment(t'\nLocal: "closure"')

        return child

    child = factory()
    assert "local" in namespace_for(child)
    # Python names and DSL names are case sensitive; explicit bindings can alias them.
    result = render(child, namespace={"Local": strong})
    assert result.body == "<strong>closure</strong>"


def test_callable_return_callable_dispatch_caller_children_structure_and_namespace() -> None:
    seen: list[Children] = []

    def child(*, children: Children) -> Fragment:
        seen.append(children)

        return html(t"\narticle: {children}", namespace={"Caller": em})

    def app() -> Fragment:

        return html(t'\nChild:\n  Caller: "owned"', namespace={"Child": child, "Caller": strong})

    result = render(app)
    assert result.body == "<article><strong>owned</strong></article>"
    assert isinstance(seen[0].nodes[0], Element)
    assert seen[0].nodes[0].tag == "Caller"


@pytest.mark.parametrize(
    "body",
    ['\nspan: "one"', '\nspan: "one"\nspan: "two"', '\nfragment:\n  span: "one"\n  span: "two"'],
)
def test_callable_return_callable_dispatch_root_exposure(body: str) -> None:
    def child() -> Template:

        return Template(body)

    def app() -> Fragment:

        return html(t"\nChild:", namespace={"Child": child})

    result = render(app)
    assert result.body.count("<span>") == body.count("span:")
    assert "fragment" not in result.body


def test_callable_return_callable_dispatch_invalid_return() -> None:
    def app() -> Fragment:

        return html(t"\nBad:", namespace={"Bad": lambda: "invalid"})

    with pytest.raises(TypeError, match="Template or Fragment"):
        render(app)


def test_callable_return_callable_dispatch_imported_alias() -> None:
    def app() -> Fragment:

        return html(t"\nImported:", namespace={"Imported": imported_app})

    result = render(app)
    assert "Counter" in result.body
    assert result.handlers


def test_callable_return_callable_dispatch_no_frame_retention() -> None:
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


def test_callable_return_callable_dispatch_leaves_native_tags_unshadowed() -> None:
    def shadow() -> Fragment:

        return html(t'\nspan: "shadow"')

    reserved = ("div", "object", "template", "var", "math")

    def main() -> Fragment:

        return html(
            t"""
                main:
                  html: "native"
                  div:
                    template: "x"
                  object:
                    var: "y"
                  math: "z"
            """,
            namespace=dict.fromkeys(reserved, shadow),
        )

    assert render(main).body == (
        "<main><html>native</html><div><template>x</template></div>"
        "<object><var>y</var></object><math>z</math></main>"
    )


def test_callable_return_lowercase_alias() -> None:
    def app() -> Fragment:

        return html(t'\ncard(title="lowercase"):', namespace={"card": sample})

    result = render(app)
    assert result.body.startswith("<section>")
    assert "lowercase" in result.body


def test_callable_return_props_and_escaped_strings() -> None:
    from pysx import Signal, signal

    live = signal("initial")
    seen: list[Signal[str]] = []

    def child(
        *,
        value: Signal[str],
        label: str = "default",
        onClick: str,  # noqa: N803 - DSL spelling
    ) -> Template:
        seen.append(value)

        return t"\nspan: {value}; {label}; {onClick}"

    def app() -> Fragment:

        return html(
            t"\nChild(value={live}, onClick={'<script>unsafe</script>'})",
            namespace={"Child": child},
        )

    result = render(app)
    assert seen == [live]
    assert "default" in result.body
    assert "&lt;script&gt;unsafe&lt;/script&gt;" in result.body
    assert result.handlers == {}
    live.set("updated")
    assert any(op.get("v") == "updated" for watcher in result.watchers for op in watcher.refresh())


@pytest.mark.parametrize("attrs", ["", '(label="extra")'])
def test_callable_return_missing_or_unexpected_props(attrs: str) -> None:
    def child(*, title: str) -> Template:

        return t"\nspan: {title}"

    def app() -> Fragment:

        return html(Template(f"\nChild{attrs}:"), namespace={"Child": child})

    with pytest.raises(TypeError):
        render(app)


def test_component_tags_use_binds_and_validates() -> None:
    from typing import Any

    def card(*, label: str) -> Template:

        return t"\nstrong: {label}"

    def app() -> Fragment:

        return html(t'\ncard(label="local"):', use=(card,))

    body = render(app).body
    assert body.startswith("<strong>")
    assert "local" in body
    invalid: Any = ("card",)

    with pytest.raises(TypeError, match="use= takes components"):
        html(t'\np: "x"', use=invalid)


@pytest.mark.parametrize("form", ["use", "hole", "namespace"])
def test_template_import_usage_real_checkers(tmp_path: Path, form: str) -> None:
    """Template references are genuine usages, rather than unused-import exemptions."""
    import json
    import subprocess
    import sys

    (tmp_path / "pyrightconfig.json").write_text(
        json.dumps(
            {
                "typeCheckingMode": "strict",
                "pythonVersion": "3.14",
                "extraPaths": [str(Path.cwd())],
                "venvPath": str(Path.cwd()),
                "venv": ".venv",
            }
        )
    )
    (tmp_path / "shared.py").write_text(
        "from string.templatelib import Template\n"
        "def Card() -> Template:\n    return t'\\nstrong: \"ordinary import\"'\n"
    )
    source = tmp_path / "usage.py"
    source.write_text(
        "from pysx import Fragment, html, render\n"
        "from shared import Card\n"
        "from pathlib import PurePath\n"
        "def app() -> Fragment:\n"
        + {
            "use": "    return html(t'\\nCard:', use=(Card,))\n",
            "hole": "    return html(t'\\n{Card}:')\n",
            "namespace": "    return html(t'\\nShared:', namespace={'Shared': Card})\n",
        }[form]
        + "assert render(app).body == '<strong>ordinary import</strong>'\n"
    )

    for checker in ("ruff", "pyright"):
        args = [sys.executable, "-m", checker]

        if checker == "ruff":
            args += ["check", "--select", "F401", "--output-format", "json"]
        checked = subprocess.run(
            [*args, str(source)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        output = checked.stdout + checked.stderr
        assert checked.returncode == 1, output
        assert "PurePath" in output, output
        assert "Card" not in output, output
    fixed = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "F401", "--fix", str(source)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert "from shared import Card" in source.read_text()
    assert "PurePath" not in source.read_text()

    for command in (
        [sys.executable, "-m", "ruff", "check", "--select", "F401", str(source)],
        [sys.executable, "-m", "pyright", str(source)],
        [sys.executable, str(source)],
    ):
        checked = subprocess.run(
            command, cwd=tmp_path, capture_output=True, text=True, check=False, timeout=60
        )
        assert checked.returncode == 0, checked.stdout + checked.stderr


def test_composition_recursive_shared_panel_and_checker(tmp_path: Path) -> None:
    from examples.components.composition import tree_controls
    from pysx.check import diagnostics

    result = render(tree_controls)

    try:
        assert result.body.startswith("<section")
        assert 'role="tree"' in result.body
        assert 'id="tree-library"' in result.body
        assert "padding: 16px" in result.css
        assert "background: var(--surface)" in result.css
        assert diagnostics(Path("examples/components/composition.py")) == []
        fixture = tmp_path / "namespace.py"
        fixture.write_text(
            "from pysx import html, strong\n"
            "def app():\n"
            "    return html(t'''\n        Alias: \"ok\"\n        Missing:\n''',"
            " namespace={'Alias': strong})\n"
        )
        problems = diagnostics(fixture)
        assert len(problems) == 1
        assert "Missing" in problems[0]["message"]
        fixture.write_text(
            "from pysx import html, local_state\n"
            "def app():\n    count = local_state('count', 0)\n"
            "    return html(t'\\nspan: {count()}')\n"
        )
        problems = diagnostics(fixture)
        assert len(problems) == 1
        assert "evaluated once and frozen" in problems[0]["message"]
    finally:
        result.dispose()


def test_component_tags_direct_reference_forms_and_content() -> None:
    from functools import partial
    from types import SimpleNamespace

    from pysx import native, styled

    def local(*, label: str, children: Children) -> Fragment:

        return html(t"\nsection:\n  {label}\n  {children}")

    alias = local
    fixed = partial(alias, label="partial")
    shared = SimpleNamespace(Card=styled.div(t"padding: 4px"))
    result = render(
        lambda: html(t"""
        {local}(label="local"):
          {shared.Card}:
            {native.Strong}: "nested"
        {fixed}:
          span: "partial-child"
        {native.Br}()
        {42}
        p: {local}
    """)
    )
    assert "<strong>nested</strong>" in result.body
    assert "partial-child" in result.body
    assert "<br>" in result.body
    assert "42" in result.body
    assert "function" in result.body


def test_component_tags_structure_cache_never_keeps_values() -> None:
    from pysx import native

    first = render(lambda: html(t'\n{native.Strong}: "one"'))
    second = render(lambda: html(t'\n{native.Em}: "one"'))
    assert first.body == "<strong>one</strong>"
    assert second.body == "<em>one</em>"


@pytest.mark.parametrize("value", [None, 42, "div", ["div"]])
def test_component_tags_unsupported_runtime_values(value: object) -> None:
    with pytest.raises(TypeError, match=f"received {type(value).__name__}"):
        render(lambda: Fragment(t'\n{value}: "child"'))
