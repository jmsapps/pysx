"""Frozen observable example inventory and its executable public equivalents."""

import asyncio
import importlib
from pathlib import Path

import pytest

from examples import examples
from pysx import Application, HostConfig, RequestContext
from pysx.tooling.check import diagnostics, diagnostics_for_source

# One row per observable source example, independent of any external checkout.
# Browser scripts assert interactions; SSR alone is deliberately insufficient.
CORPUS = {
    "greeting": ("hello", "browser_hello.mjs"),
    "signals": ("reactive_state", "browser_operators.mjs"),
    "conditional_branches": ("templates", "browser_templates.mjs"),
    "iteration": ("templates", "browser_templates.mjs"),
    "forms": ("forms", "browser_forms.mjs"),
    "todos": ("todos", "browser_todos.mjs"),
    "operators": ("operators", "browser_operator_catalog.mjs"),
    "effects": ("effects", "browser_effects.mjs"),
    "boolean_attributes": ("attributes", "browser_attributes.mjs"),
    "element_attributes": ("attributes", "browser_attributes.mjs"),
    "keyed_attributes": ("attributes", "browser_attributes.mjs"),
    "keyed_diffs": ("keyed_rows", "browser_keyed_rows.mjs"),
    "styling": ("themes", "browser_themes.mjs"),
    "routing": ("navigation", "browser_navigation_example.mjs"),
    "wildcard_routes": ("navigation", "browser_navigation_example.mjs"),
    "relative_navigation": ("navigation", "browser_navigation_example.mjs"),
    "links": ("navigation", "browser_navigation_example.mjs"),
    "combobox": ("events", "browser_keyboard.mjs"),
    "treeview": ("composition", "browser_tree.mjs"),
    "focus_keyboard": ("events", "browser_keyboard.mjs"),
}
ROOT = Path(__file__).resolve().parents[1]


def test_corpus_executable_inventory_and_browser_registration() -> None:
    assert len(CORPUS) == 20
    runner = (ROOT / "tests/browser_runner.mjs").read_text()

    for example, script in CORPUS.values():
        assert example in examples
        assert f'"{example}", "{script}"' in runner
        source = (ROOT / "tests" / script).read_text()
        assert "PASSED" in source
        assert "assert" in source


@pytest.mark.parametrize("case", CORPUS)
def test_corpus_executable_ssr_and_owned_cleanup(case: str) -> None:
    name, _ = CORPUS[case]
    view = importlib.import_module(f"examples.{name}").app

    async def exercise() -> None:
        host = Application(view, config=HostConfig(recovery=False))

        try:
            response = await host.http(RequestContext(headers={"host": "localhost"}))
            assert response.status == 200
            assert b"pysx-root" in response.body
            assert response.adoption is not None
        finally:
            await host.close()
        assert not host.runtime.graphs

    asyncio.run(exercise())


@pytest.mark.parametrize("name", ["hello", "attributes", "effects"])
def test_corpus_executable_examples_are_checker_clean(name: str) -> None:
    assert diagnostics(ROOT / f"examples/{name}.py") == []


@pytest.mark.parametrize("attribute", ["hreff", "href"])
def test_corpus_executable_advisory_attribute_rejections(attribute: str) -> None:
    source = f'from pysx import pysx\nview = pysx(t\'div({attribute}="/"): "Example"\')\n'
    findings = diagnostics_for_source(source)
    assert findings
    assert any(attribute in finding["message"] for finding in findings)
