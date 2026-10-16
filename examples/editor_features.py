"""Drive the editor language features over a frozen sample without executing it."""

from pathlib import Path
from typing import TYPE_CHECKING, cast

from pysx import Fragment, component, derived, each, pysx, signal, when
from pysx.lsp_edits import apply_edits, format_edits
from pysx.lsp_features import (
    Context,
    TemplateCall,
    classify,
    completions,
    document_symbols,
    semantic_tokens,
    signature,
    template_calls,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

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
)
from .components.diagnostics import SourceCode

SAMPLE = '''from pysx import Fragment, pysx


def Panel(*, title: str, tone: str = "calm") -> Fragment:
    return pysx(t"p: {title}")


def view(rows, ready) -> Fragment:
    return pysx(
        t"""
        section(class="board"):
              if {ready}:
                    Panel(title="Ready")
              for row in {rows}:
                   li(id="row"): {row}
        """,
    )
'''

FILENAME = str(Path(__file__).with_name("_editor_sample.py"))

type Symbol = tuple[str, str, object, object, list[object]]


def Panel(*, title: str, tone: str = "calm") -> Fragment:
    """The component the sample refers to, so the rendered half really runs."""

    return pysx(t"p(class={tone}): {title}")


def _calls() -> list[TemplateCall]:
    return list(template_calls(SAMPLE, FILENAME, Path(FILENAME).parent, {FILENAME: SAMPLE}))


def tokens_report() -> str:
    found = semantic_tokens(_calls())
    lines = [f"{SAMPLE[span.start : span.end]!r:>12}  {kind}" for span, kind, _ in found[:12]]

    return "\n".join(lines) or "no markup tokens"


def outline_report(entries: Sequence[object], depth: int = 0) -> str:
    """Render the markup symbol tree; callers pass document_symbols() directly."""
    out: list[str] = []

    for entry in entries:
        name, kind, _span, _selection, children = cast("Symbol", entry)
        out.append(f"{'  ' * depth}{name}  ({kind})")
        out.extend(outline_report(children, depth + 1).splitlines())

    return "\n".join(out)


def _at(marker: str, offset: int = 0) -> int:
    return SAMPLE.index(marker) + offset


def _context(marker: str, offset: int = 0) -> Context | None:
    return classify(
        SAMPLE, _at(marker, offset), FILENAME, Path(FILENAME).parent, {FILENAME: SAMPLE}
    )


def completion_report() -> str:
    context = _context('Panel(title="Ready"', len("Panel("))

    if context is None:
        return "no owned context at the caret"
    labels = [item.label for item in completions(context)]

    return ", ".join(labels) or "no completions"


def hover_report() -> str:
    context = _context('Panel(title="Ready"', len("Panel("))
    help_result = signature(context) if context is not None else None

    if help_result is None or not help_result.signatures:
        return "no signature at the caret"

    return help_result.signatures[0].label


def navigation_report() -> str:
    context = _context("Panel(title=")

    if context is None or context.tag_span is None:
        return "no tag at the caret"
    owner = context.scope.owner(context.tag)
    line = SAMPLE[: context.tag_span.start].count("\n") + 1

    return (
        f"{context.tag!r} used on line {line}; "
        f"declared in this module: {'yes' if owner is not None else 'no'}"
    )


def formatted_sample() -> str:
    calls = _calls()

    return apply_edits(SAMPLE, [edit for call in calls for edit in format_edits(call)])


@component
def app() -> Fragment:
    view = signal("tokens")
    rows = signal(["alpha", "beta"])
    ready = signal(True)
    panels = {
        "tokens": tokens_report,
        "outline": lambda: outline_report(document_symbols(_calls())),
        "completion": completion_report,
        "hover": hover_report,
        "navigation": navigation_report,
        "formatted": formatted_sample,
    }
    shown = derived(lambda: panels[view()]())
    changed = derived(lambda: "yes" if formatted_sample() != SAMPLE else "no")

    def select(name: str) -> Callable[[object], None]:
        def handler(_event: object) -> None:
            view.set(name)

        return handler

    # each() is the recommended row surface: the builder is a callback, so the
    # row value exists when it runs rather than when the t-string is evaluated.
    buttons = each(
        sorted(panels),
        lambda name: pysx(
            t'Action(type="button", id={f"show-{name}"}, onClick={select(name)}): {name}',
            namespace={"Action": Action},
        ),
        key=lambda name: name,
    )
    live_rows = each(
        rows,
        lambda row: pysx(t'li(class="row"): {row}'),
        key=lambda row: row,
    )
    banner = when(
        conditions=[(ready, lambda: Panel(title="Ready"))],
        default=lambda: Panel(title="Waiting", tone="muted"),
    )

    def add(_event: object) -> None:
        rows.set([*rows(), f"row {len(rows()) + 1}"])

    def toggle(_event: object) -> None:
        ready.set(not ready())

    return pysx(
        t"""
        Page(id="container"):
            Eyebrow: "pysx / examples"
            Title: "Editor features"
            Description:
                "The language server reads this sample statically. It is never imported or run."
            Actions: {buttons}
            Card:
                SourceCode(id="panel"): {shown}
            Metric:
                "Formatting would change the sample: "
                Value(id="would-change"): {changed}
            Title: "The same markup, rendered live"
            Actions:
                Action(type="button", id="add-row", onClick={add}): "Add row"
                Action(type="button", id="toggle", onClick={toggle}): "Toggle ready"
            Card(id="live"):
                {banner}
                ul(id="rows"): {live_rows}
        """,
    )
