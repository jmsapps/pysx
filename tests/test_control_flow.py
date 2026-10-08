"""Explicit lexical environments and owned DSL control flow."""

from typing import TYPE_CHECKING

import pytest

from pysx import (
    Binding,
    Fragment,
    Signal,
    bounded_while,
    defer,
    defer2,
    local_state,
    on_cleanup,
    pysx,
    render,
    signal,
)
from pysx.parser import InterpolationError, PysxSyntaxError, parse
from pysx.server import Session

if TYPE_CHECKING:
    from collections.abc import Callable


def test_render_snapshot_comprehension_and_while_builders() -> None:
    count = signal(2)
    fragments = [pysx(t"\nspan: {index}") for index in range(count())]
    predicate = iter([True, False])
    ordinary = bounded_while(lambda: next(predicate), lambda: t'\nstrong: "while"')
    session = Session(lambda: pysx(t"\ndiv: {fragments}; {ordinary}"))
    assert session.rendered.body.count("<span>") == 2
    assert "<strong>while</strong>" in session.rendered.body
    assert session.rendered.watchers == []
    count.set(3)
    assert session.pending == []
    session.dispose()


def test_render_snapshot_bounds() -> None:
    with pytest.raises(ValueError, match="10000"):
        render(lambda: pysx(t"\ndiv: {list(range(10001))}"))
    nested: list[object] = []
    nested.append(nested)
    with pytest.raises(ValueError, match="128"):
        render(lambda: pysx(t"\ndiv: {nested}"))
    wrapped: list[object] = []
    wrapped.append(pysx(t"\nif {True}:\n  div: {wrapped}"))
    with pytest.raises(ValueError, match="128"):
        render(lambda: pysx(t"\ndiv: {wrapped}"))


def test_branches_loops_chain_and_match_lifetimes() -> None:
    first = signal(True)
    second = signal(False)
    value = signal("a")
    text = signal("live")

    def app() -> Fragment:

        return pysx(t"""
            if {first}:
              p: {text}
            elif {second}:
              p: "second"
            else:
              p: "last"
            match {value}:
              case "a" | "b":
                span: "pair"
              case _:
                span: "other"
        """)

    session = Session(app)
    assert "live" in session.rendered.body
    assert "pair" in session.rendered.body
    first.set(False)
    session.pending.clear()
    text.set("inactive")
    assert session.pending == []
    second.set(True)
    assert any("second" in str(op) for op in session.pending)
    value.set("z")
    assert any("other" in str(op) for op in session.pending)
    session.dispose()
    assert not first.observers
    assert not second.observers
    assert not value.observers


def test_branches_loops_keyed_capture_destructure_and_locals() -> None:
    rows = signal([(1, "one"), (2, "two")])
    index = Binding[int]("index")
    row = Binding[str]("row")
    label = Binding[str]("label")
    clicked: list[str] = []
    discarded: list[str] = []

    def capture(value: str) -> Callable[[object], None]:

        return lambda _event: clicked.append(value)

    def app() -> Fragment:

        return pysx(
            t"""
            for (index, row) in {rows} key={index}:
              let label = {defer(row, str.upper)}
              set label = {defer2(label, index, lambda text, i: f"{i}:{text}")}
              discard {defer(label, discarded.append)}
              button(title={label}, onClick={defer(row, capture)}): {label}
        """,
            namespace={"index": index, "row": row, "label": label},
        )

    session = Session(app)
    assert "1:ONE" in session.rendered.body
    assert "2:TWO" in session.rendered.body
    assert set(discarded) == {"1:ONE", "2:TWO"}
    before = dict(session.rendered.handlers)
    rows.set([(2, "two"), (1, "one")])
    assert set(before) == set(session.rendered.handlers)

    for handler in session.rendered.handlers.values():
        handler(None)
    assert sorted(clicked) == ["one", "two"]
    session.dispose()
    assert not rows.observers


def test_branches_loops_nested_shadow_and_snapshot_watchers() -> None:
    row = Binding[int]("row")
    fragment = pysx(
        t"""
        for row in {[1, 2]}:
          p: {row}
          for row in {[3, 4]}:
            span: {row}
          strong: {row}
    """,
        namespace={"row": row},
    )
    output = render(lambda: fragment)
    assert len(output.watchers) == 0
    assert output.body.count(">1</pysx-slot>") == 2
    assert output.body.count(">3</pysx-slot>") == 2


@pytest.mark.parametrize(
    "text",
    [
        "\nelse:\n  p: 'no'",
        "\nif ",
        "\ncase 'a':\n  p: 'no'",
        "\nmatch ",
    ],
)
def test_branches_loops_invalid_structure_has_position(text: str) -> None:
    with pytest.raises(PysxSyntaxError) as error:
        parse((text,))
    assert error.value.position is not None


def test_branches_loops_invalid_chain_and_keys() -> None:
    with pytest.raises(PysxSyntaxError, match="contiguous"):
        render(
            lambda: pysx(t"""
            if {True}:
              p: "yes"
            span: "gap"
            else:
              p: "no"
        """)
        )
    row = Binding[int]("row")
    with pytest.raises(ValueError, match="duplicate"):
        render(
            lambda: pysx(
                t"""
            for row in {[1, 1]} key={row}:
              p: {row}
        """,
                namespace={"row": row},
            )
        )
    with pytest.raises(InterpolationError, match="missing lexical"):
        render(
            lambda: pysx(t"""
            for missing in {[1]}:
              p: {row}
        """)
        )
    with pytest.raises(InterpolationError, match="string or integer"):
        render(
            lambda: pysx(
                t"""
            for row in {[1]} key={None}:
              p: {row}
        """,
                namespace={"row": row},
            )
        )


def test_branches_loops_bounded_ordinary_while() -> None:
    values = iter([True, True, False])
    assert bounded_while(lambda: next(values), lambda: "row", limit=2) == ("row", "row")
    with pytest.raises(ValueError, match="exceeded"):
        bounded_while(lambda: True, lambda: "row", limit=2)
    with pytest.raises(ValueError, match="between"):
        bounded_while(lambda: False, lambda: "row", limit=10001)


@pytest.mark.parametrize("keyed", [True, False])
def test_branches_loops_owner_identity_and_cleanup(keyed: bool) -> None:
    rows = signal(["a", "b"])
    row = Binding[str]("row")
    observed: dict[str, Signal[int]] = {}
    cleaned: list[str] = []

    def row_component(value: str) -> Fragment:
        state = local_state("count", 0)
        observed[value] = state
        on_cleanup(lambda: cleaned.append(value))

        return pysx(t"\nspan: {state}")

    def app() -> Fragment:
        if keyed:
            template = t"""
                for row in {rows} key={row}:
                  row_component(value={row}):
            """
        else:
            template = t"""
                for row in {rows}:
                  row_component(value={row}):
            """

        return pysx(template, use=(row_component,), namespace={"row": row})

    session = Session(app)
    a, b = observed["a"], observed["b"]
    rows.set(["b", "a"])
    assert observed["a"] is (a if keyed else b)
    assert observed["b"] is (b if keyed else a)
    rows.set(["b"])
    assert cleaned
    rows.set(["b", "a"])
    assert observed["a"] is not (a if keyed else b)
    session.dispose()
    assert not rows.observers


def test_branches_loops_conditional_attributes_and_metadata() -> None:
    row = Binding[int]("row")
    output = render(
        lambda: pysx(
            t"""
        for row in {[0, 1]}:
          p(title={defer(row, lambda n: "yes" if n else None)}): {row:02d}
    """,
            namespace={"row": row},
        )
    )
    assert output.body.count('title="yes"') == 1
    assert ">00</pysx-slot>" in output.body
    assert ">01</pysx-slot>" in output.body
    with pytest.raises(InterpolationError) as error:
        render(
            lambda: pysx(
                t"""
            for row in {[1]!r}:
              p: {row}
        """,
                namespace={"row": row},
            )
        )
    assert error.value.position is not None


def test_branches_loops_unbound_assignment_and_limits_have_diagnostics() -> None:
    row = Binding[int]("row")
    with pytest.raises(InterpolationError) as error:
        render(
            lambda: pysx(
                t"""
            set row = {1}
            p: {row}
        """,
                namespace={"row": row},
            )
        )
    assert error.value.position is not None
    assert "unbound lexical" in str(error.value)
    with pytest.raises(ValueError, match="10000"):
        render(
            lambda: pysx(
                t"""
            for row in {range(10001)}:
              p: {row}
        """,
                namespace={"row": row},
            )
        )
    with pytest.raises(PysxSyntaxError, match="indented body"):
        render(
            lambda: pysx(
                t"""
            for row in {[1]}:
        """,
                namespace={"row": row},
            )
        )
