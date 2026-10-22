"""Inspect and repair static template samples without executing their source."""

from pysx import Fragment, component, derived, pysx, signal
from pysx.tooling.check import apply_fix, diagnostics_for_source

from .components import (
    Action,
    Actions,
    Card,
    Description,
    Eyebrow,
    Metric,
    Page,
    Title,
    Value,
    example_app,
    example_title,
)
from .components.diagnostics import SourceCode

FROZEN = """from pysx import pysx, signal
count = signal(0)
view = pysx(t"p: {count()}")
"""
ATTRIBUTE = """from pysx import pysx
view = pysx(t"div(hreff='advisory')")
"""


def _report(source: str) -> str:
    findings = diagnostics_for_source(source)

    return (
        "\n".join(f"{item['code']}: {item['message']}" for item in findings)
        or "No diagnostics ([])"
    )


@component
def app() -> Fragment:
    example_title("Diagnostics")

    sample = signal(FROZEN)
    count = signal(0)
    report = derived(lambda: _report(sample()))
    no_fix = derived(
        lambda: not any(item.get("fixes") for item in diagnostics_for_source(sample()))
    )

    def fix(_event: object) -> None:
        source = sample()

        for finding in diagnostics_for_source(source):
            fixes = finding.get("fixes", [])

            if fixes:
                sample.set(apply_fix(source, fixes[0]))

                return

    def warning(_event: object) -> None:
        sample.set(ATTRIBUTE)

    def reset(_event: object) -> None:
        sample.set(FROZEN)

    def increment(_event: object) -> None:
        count.update(lambda current: current + 1)

    return pysx(t"""
        Page:
            header:
                Eyebrow: "pysx / examples"
                Title: "Diagnostics"
                Description:
                    "Inspect a source sample, apply a suggested fix, or try an advisory warning."
                    " Samples are analyzed as text."
            Card:
                h2: "Source sample"
                SourceCode(id="sample-source"): {sample}
                SourceCode(id="sample-findings", role="status", aria-live="polite"): {report}
                Actions:
                    Action(
                        id="apply-fix", disabled={no_fix}, onClick={fix}
                    ): "Apply suggested fix"
                    Action(id="show-warning", onClick={warning}): "Show attribute warning"
                    Action(id="reset-sample", onClick={reset}): "Reset sample"
            Metric:
                "Live counter: "
                Value(id="live-count"): {count}
            Actions:
                Action(id="increment-counter", onClick={increment}): "Increment"
    """)


example = example_app(app)
