"""Real source imports and sourceless builds share lexical fragment ownership."""

import importlib
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from pysx import Fragment, render
from pysx.build import build_tree
from pysx.loader import CodeCache, SourceLoader, import_app, install_loader

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@pytest.fixture
def app_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "source"
    package = root / "author_app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "path", [str(root), *sys.path])
    importlib.invalidate_caches()
    finders = tuple(sys.meta_path)
    yield root
    sys.meta_path[:] = finders

    for name in tuple(sys.modules):
        if name == "author_app" or name.startswith("author_app."):
            del sys.modules[name]


def _write(root: Path, name: str, source: str) -> Path:
    path = root / "author_app" / name
    path.write_text(source, encoding="utf-8")

    return path


def _app(module: object, attr: str = "app") -> Callable[[], Fragment]:
    return cast("Callable[[], Fragment]", getattr(module, attr))


def test_import_closures_and_cross_module_origin(app_root: Path) -> None:
    _write(
        app_root,
        "producer.py",
        """from pysx import pysx, styled
Panel = styled.section(t"color: red")
fragment = pysx(t"Panel: 'Producer'")
def factory():
    Local = styled.strong(t"color: blue")
    return lambda: pysx(t"Local: 'Closed'")
row = factory()
Panel = styled.aside(t"color: green")
""",
    )
    path = _write(
        app_root,
        "view.py",
        """from pysx import pysx, styled
from .producer import fragment, row
Panel = styled.div(t"color: purple")
def app():
    return pysx(t"main: {fragment}; {row()}")
""",
    )
    module = import_app("author_app.view")
    assert module.__name__ == "author_app.view"
    assert module.__file__ == str(path)
    result = render(_app(module))
    assert "<section" in result.body
    assert "Producer" in result.body
    assert "<strong" in result.body
    assert "Closed" in result.body
    assert "<aside" not in result.body
    assert result.styles is not None
    assert "color:red" in result.styles.snapshot().replace(" ", "")


def test_caller_children_and_styled_wrapper_preserve_origin(app_root: Path) -> None:
    _write(
        app_root,
        "components.py",
        """from pysx import pysx, styled
def wrapper(*, children):
    return pysx(t"article: {children}")
Wrapper = styled(wrapper)(t"color: red")
Panel = styled.aside(t"color: blue")
""",
    )
    _write(
        app_root,
        "view.py",
        """from pysx import pysx, styled
from .components import Wrapper
Panel = styled.strong(t"color: green")
def app():
    return pysx(t"\\nWrapper:\\n  Panel: 'Caller'")
""",
    )
    result = render(_app(import_app("author_app.view")))
    assert "<article" in result.body
    assert "<strong" in result.body
    assert "<aside" not in result.body


def test_absent_inactive_component_fails_only_when_selected(app_root: Path) -> None:
    _write(
        app_root,
        "view.py",
        """from pysx import pysx
enabled = False
def app():
    return pysx(t"\\nif {enabled}:\\n  Missing: 'Absent'\\nelse:\\n  p: 'Present'")
""",
    )
    module = import_app("author_app.view")
    assert "Present" in render(_app(module)).body
    module.__dict__["enabled"] = True
    with pytest.raises(NameError, match="Missing"):
        render(_app(module))


def test_source_scope_and_repeat_install(app_root: Path) -> None:
    _write(app_root, "view.py", "from pysx import pysx\ndef app(): return pysx(t\"p: 'Hi'\")\n")
    finder = install_loader(packages=("author_app",))
    assert install_loader(packages=("author_app",)) is finder
    assert not finder.covers("pysx.render", Path("/work/pysx/render.py"))
    assert not finder.covers("outside", Path("/work/outside.py"))
    assert not finder.covers("author_app.vendor.x", Path("/work/vendor/x.py"))
    module = importlib.import_module("author_app.view")
    first = module.app.__code__
    importlib.reload(module)
    assert module.app.__code__ is first
    assert _app(module)().bound


def test_late_install_is_reported(app_root: Path) -> None:
    _write(app_root, "view.py", "from pysx import pysx\ndef app(): return pysx(t\"p: 'Hi'\")\n")
    importlib.import_module("author_app.view")
    with pytest.raises(RuntimeError, match=r"before importing author_app\.view"):
        install_loader(packages=("author_app",))


def test_dependency_change_invalidates_cached_marker(app_root: Path) -> None:
    marker = _write(app_root, "marker.py", "from unrelated import pysx\n")
    view = _write(
        app_root,
        "view.py",
        "from .marker import pysx\nPanel = object()\nview = pysx(t\"Panel: 'Hi'\")\n",
    )
    cache = CodeCache(entries=2)
    source = view.read_text(encoding="utf-8")
    first = cache.compile(source, str(view), "author_app", (app_root,))
    assert cache.compile(source, str(view), "author_app", (app_root,)) is first
    marker.write_text("from pysx import pysx\n", encoding="utf-8")
    second = cache.compile(source, str(view), "author_app", (app_root,))
    assert second is not first
    namespace: dict[str, object] = {}
    # Execute without importing the deliberately fake marker package.
    source = "from pysx import pysx\nPanel = object()\nview = pysx(t\"Panel: 'Hi'\")\n"
    exec(cache.compile(source, str(view), "author_app", (app_root,)), namespace)
    assert isinstance(namespace["view"], Fragment)
    assert namespace["view"].bound


def test_build_source_free_parity_and_original_metadata(app_root: Path, tmp_path: Path) -> None:
    _write(
        app_root,
        "view.py",
        """from pysx import pysx, styled
Panel = styled.section(t"color: red")
def app():
    return pysx(t"Panel: {pysx(t'p: {42!r}')} {43:>5}")
def broken():
    raise RuntimeError("original line")
""",
    )
    output = tmp_path / "bundle" / "author_app"
    paths = build_tree(app_root / "author_app", output)
    assert len(paths) == 2
    (app_root / "author_app" / "asset.txt").write_text("asset", encoding="utf-8")
    other = tmp_path / "assets" / "author_app"
    build_tree(app_root / "author_app", other)
    assert (other / "asset.txt").read_text(encoding="utf-8") == "asset"
    shutil.rmtree(app_root)
    script = """import sys, json, traceback
sys.path.insert(0, sys.argv[1])
from author_app.view import app, broken
from pysx import render
fragment = app()
assert fragment.bound
nested = fragment.template.interpolations[0]
assert nested.expression == "pysx(t'p: {42!r}')"
assert nested.value.bound
assert nested.value.template.interpolations[0].conversion == 'r'
assert fragment.template.interpolations[1].format_spec == '>5'
assert '<section' in render(app).body
try:
    broken()
except RuntimeError:
    text = traceback.format_exc()
    assert 'author_app/view.py' in text
    assert 'raise RuntimeError("original line")' in text
print(json.dumps({'file': app.__code__.co_filename, 'bound': fragment.bound}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(output.parent)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {"file": "author_app/view.py", "bound": True}


def test_inline_siblings_source_loader_and_portable_build(app_root: Path, tmp_path: Path) -> None:
    _write(
        app_root,
        "components.py",
        """from pysx import styled
Break = styled.br(t"")
""",
    )
    _write(
        app_root,
        "view.py",
        """from pysx import pysx
from .components import Break as lower
def app():
    return pysx(t"h2: 'Live'; lower; lower;")
""",
    )
    result = render(_app(import_app("author_app.view")))
    assert result.body.startswith("<h2>Live</h2>")
    assert result.body.count("<br") == 2
    result.dispose()
    output = tmp_path / "bundle" / "author_app"
    build_tree(app_root / "author_app", output)
    shutil.rmtree(app_root)
    script = """import sys
sys.path.insert(0, sys.argv[1])
from author_app.view import app
from pysx import render
fragment = app()
assert fragment.bound
assert 'lower' in fragment.namespace
result = render(app)
assert result.body.startswith('<h2>Live</h2>')
assert result.body.count('<br') == 2
result.dispose()
"""
    subprocess.run([sys.executable, "-c", script, str(output.parent)], check=True, timeout=30)


def test_original_loader_traceback(app_root: Path) -> None:
    path = _write(
        app_root,
        "view.py",
        """from pysx import pysx
def app():
    value = 1 / 0
    return pysx(t"p: {value}")
""",
    )
    module = import_app("author_app.view")
    with pytest.raises(ZeroDivisionError) as error:
        _app(module)()
    text = "".join(traceback.format_exception(error.value))
    assert str(path) in text
    assert "value = 1 / 0" in text


def test_installed_wheel_runs_sourceless_app(app_root: Path, tmp_path: Path) -> None:
    _write(
        app_root,
        "view.py",
        """from pysx import pysx, styled
Panel = styled.section(t"color: red")
def app():
    return pysx(t"Panel: 'Installed'")
""",
    )
    bundle = tmp_path / "deployment"
    build_tree(app_root / "author_app", bundle / "author_app")
    shutil.rmtree(app_root)
    project = Path(__file__).resolve().parents[1]
    artifacts = tmp_path / "wheels"
    subprocess.run(
        ["uv", "build", "--offline", "--wheel", "--out-dir", str(artifacts)],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(artifacts.glob("*.whl"))
    installed = tmp_path / "installed"
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--python",
            sys.executable,
            "--target",
            str(installed),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    script = """from pathlib import Path
import sys
import pysx
from author_app.view import app
assert Path(pysx.__file__).is_relative_to(Path(sys.argv[1]))
assert app.__code__.co_filename == 'author_app/view.py'
assert app().bound
assert '<section' in pysx.render(app).body
assert 'Installed' in pysx.render(app).body
"""
    subprocess.run(
        [sys.executable, "-S", "-c", script, str(installed)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(installed), str(bundle)))},
        check=True,
        capture_output=True,
        text=True,
    )


def test_finder_uses_namespace_package_paths(app_root: Path) -> None:
    (app_root / "author_app" / "__init__.py").unlink()
    _write(
        app_root, "view.py", "from pysx import pysx\ndef app(): return pysx(t\"p: 'Namespace'\")\n"
    )
    module = import_app("author_app.view")
    assert isinstance(module.__loader__, SourceLoader)
    assert _app(module)().bound
    assert "Namespace" in render(_app(module)).body
