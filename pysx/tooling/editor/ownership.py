"""Reversible editor-ownership setup: what pysx takes over, and how to give it back.

The decisions live here rather than in a client so that setup, rollback and
guarded restoration are provable without an editor host. A caller observes the
current settings, applies the returned steps in order and persists the retained
state; nothing here touches an editor or the filesystem except the project
configuration edit, which is computed, not written.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypedDict, cast

from . import config as _editor_config
from .config import configuration_edit

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from .config import ConfigurationEdit

PROJECTION_GLOB = _editor_config.MIRRORS
SUPPRESSED = _editor_config.RULES
RUFF_IGNORED = ("F401", "F811", "F821", "F823", "F841", "TC")
DISABLED_ACTIONS = (
    "source.fixAll",
    "source.fixAll.pylance",
    "source.fixAll.ruff",
    "source.organizeImports",
    "source.organizeImports.ruff",
    "source.unusedImports",
)
# Each rule pysx silences must already be replaced by a rule pysx owns.
REPLACEMENT = {
    "reportUnusedImport": "ruff",
    "reportUnusedVariable": "ruff",
    "reportUnusedFunction": "types",
}


class RetainedState(TypedDict):
    """Caller-persisted record of what pysx changed and what it replaced."""

    values: dict[str, object]
    digests: dict[str, str]


class SetupRefusedError(ValueError):
    """Raised with a user-facing reason when setup must not proceed."""


@dataclass(frozen=True)
class Step:
    key: str
    value: object
    previous: object


@dataclass(frozen=True)
class Plan:
    steps: tuple[Step, ...]
    configuration: ConfigurationEdit | None
    state: RetainedState
    notes: tuple[str, ...] = field(default=())

    @property
    def rollback(self) -> tuple[Step, ...]:
        """Undo an interrupted apply by replaying the originals in reverse."""

        return tuple(Step(step.key, step.previous, step.value) for step in reversed(self.steps))


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _merged(observed: Mapping[str, object], key: str, updates: Mapping[str, object]) -> object:
    current = observed.get(key)
    base: dict[str, object] = (
        dict(cast("dict[str, object]", current)) if isinstance(current, dict) else {}
    )

    return {**base, **updates}


def _extended(observed: Mapping[str, object], key: str, values: Sequence[str]) -> object:
    current = observed.get(key)
    # VS Code returns an explicit null for a cleared list; a default cannot replace it.
    base: list[object] = list(cast("list[object]", current)) if isinstance(current, list) else []

    return [*base, *(value for value in values if value not in base)]


def _without(observed: Mapping[str, object], key: str, value: str) -> object:
    current = observed.get(key)
    base: list[object] = list(cast("list[object]", current)) if isinstance(current, list) else []

    return [item for item in base if item != value]


def desired(observed: Mapping[str, object]) -> dict[str, object]:
    """The complete owned configuration, derived from what is observed now."""

    return {
        "python.analysis.ignore": _extended(observed, "python.analysis.ignore", [PROJECTION_GLOB]),
        "python.analysis.diagnosticSeverityOverrides": _merged(
            observed,
            "python.analysis.diagnosticSeverityOverrides",
            dict.fromkeys(SUPPRESSED, "none"),
        ),
        "python.analysis.disableTaggedHints": True,
        "python.analysis.fixAll": _without(
            observed, "python.analysis.fixAll", "source.unusedImports"
        ),
        "editor.codeActionsOnSave": _merged(
            observed,
            "editor.codeActionsOnSave",
            {
                **dict.fromkeys(DISABLED_ACTIONS, "never"),
                "source.organizeImports.pysx": "explicit",
            },
        ),
        "ruff.lint.ignore": _extended(observed, "ruff.lint.ignore", RUFF_IGNORED),
    }


def ownership_plan(
    observed: Mapping[str, object],
    root: Path,
    *,
    coverage: Mapping[str, bool],
    ready: bool,
) -> Plan:
    """Compute setup, refusing unless every silenced rule is already replaced."""

    if not ready:
        raise SetupRefusedError(
            "Workspace analysis is not ready. Wait for it to finish, then run setup again."
        )
    missing = sorted(rule for rule, owner in REPLACEMENT.items() if not coverage.get(owner, False))

    if missing:
        raise SetupRefusedError(
            "pysx cannot replace "
            + ", ".join(missing)
            + " yet, so those checks will not be disabled. Install the pinned developer "
            "tooling and restart analysis, then run setup again."
        )
    target = desired(observed)
    steps = tuple(
        Step(key, value, observed.get(key))
        for key, value in target.items()
        if observed.get(key) != value
    )
    notes = tuple(
        f"{key} already matches the owned configuration"
        for key in target
        if observed.get(key) == target[key]
    )

    return Plan(
        steps,
        configuration_edit(root),
        RetainedState(
            values={step.key: step.previous for step in steps},
            digests={key: digest(value) for key, value in target.items()},
        ),
        notes,
    )


def restore_plan(
    observed: Mapping[str, object], retained: RetainedState
) -> tuple[tuple[Step, ...], tuple[str, ...]]:
    """Give ownership back, skipping any key edited after pysx wrote it."""
    steps: list[Step] = []
    skipped: list[str] = []

    for key, original in retained["values"].items():
        stamp = retained["digests"].get(key)
        current = observed.get(key)

        if stamp is None or digest(current) != stamp:
            skipped.append(key)

            continue

        if current == original:
            continue
        steps.append(Step(key, original, current))

    return tuple(steps), tuple(skipped)
