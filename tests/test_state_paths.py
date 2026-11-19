"""One resolver decides where framework-managed application state lives."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from pysx.runtime.state_paths import (
    ENVIRONMENT,
    MARKER,
    PERSISTENT,
    TEMPORARY,
    component,
    persistent_path,
    state_root,
    temporary_path,
)


def test_application_state_precedence_is_explicit_then_environment_then_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "explicit"
    environment = tmp_path / "environment"
    project = tmp_path / "project"
    (project / "pyproject.toml").parent.mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n')
    nested = project / "src" / "deep"
    nested.mkdir(parents=True)

    monkeypatch.setenv(ENVIRONMENT, str(environment))
    assert state_root(explicit) == explicit, "an explicit root wins"
    assert state_root(start=nested) == environment, "the environment is next"

    monkeypatch.delenv(ENVIRONMENT)
    # The owning project is found the same way analysis finds it, so a nested working
    # directory resolves to the application's root rather than wherever it was launched.
    assert state_root(start=nested) == project / MARKER


def test_application_state_root_ignores_itself_without_touching_the_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n')
    monkeypatch.delenv(ENVIRONMENT, raising=False)
    monkeypatch.chdir(tmp_path)
    directory = persistent_path("demo")

    assert directory == tmp_path / MARKER / "demo" / PERSISTENT
    assert (tmp_path / MARKER / ".gitignore").read_text() == "*\n"
    assert not (tmp_path / ".gitignore").exists(), "the user's own ignore file is untouched"


def test_application_state_separates_persistent_from_disposable(tmp_path: Path) -> None:
    persistent = persistent_path("demo", directory=tmp_path)
    disposable = temporary_path("demo", "editor", directory=tmp_path)

    assert persistent == tmp_path / "demo" / PERSISTENT
    assert disposable == tmp_path / "demo" / TEMPORARY / "editor"
    assert not disposable.is_relative_to(persistent), "cleanup must not reach persistent data"


def test_application_state_isolates_applications_sharing_a_directory(tmp_path: Path) -> None:
    first = persistent_path("one", directory=tmp_path)
    second = persistent_path("two", directory=tmp_path)

    assert first != second
    assert first.parent.parent == second.parent.parent == tmp_path


@pytest.mark.parametrize(
    "namespace",
    ["../escape", "a/b", "with space", "x" * 200, "..", "módulo:app"],
)
def test_application_state_namespaces_cannot_escape_their_root(
    namespace: str, tmp_path: Path
) -> None:
    """A namespace is derived from an application identity, so it must be contained."""
    name = component(namespace)

    assert os.sep not in name
    assert name not in {"", ".", ".."}
    assert len(name) <= 80
    resolved = persistent_path(namespace, directory=tmp_path)
    assert resolved.resolve().is_relative_to(tmp_path.resolve())


def test_application_state_namespaces_stay_distinct_after_sanitising() -> None:
    """Two identities that sanitise to the same text must not share a directory."""
    assert component("a/b") != component("a-b")
    assert component("app") == "app", "an already-safe identity stays readable"
