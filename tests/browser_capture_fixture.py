"""Ordinary components used by local-state capture tests."""

from typing import TYPE_CHECKING, cast

from pysx import derived, each, local_state, native, pysx, signal

if TYPE_CHECKING:
    from pysx import Fragment, Signal

held: dict[str, Signal[object]] = {}
shared = signal(99)


def native_app(initial: object = 0) -> Fragment:
    count = signal(initial)
    draft = local_state("draft", "")
    held["count"] = count
    held["draft"] = cast("Signal[object]", draft)

    return native.Div(native.P(count), native.Input(bind_value=draft))


def dsl_app() -> Fragment:
    count = signal(0)
    draft = local_state("draft", "")
    computed = derived(lambda: count() + shared())
    held["count"] = cast("Signal[object]", count)
    held["draft"] = cast("Signal[object]", draft)

    return pysx(t"""
        section:
            p: {count}
            input(bind_value={draft})
            p: {computed}
    """)


def row(value: tuple[str, str]) -> Fragment:
    draft = local_state("draft", value[1])
    held[value[0]] = cast("Signal[object]", draft)

    return native.P(draft)


def keyed_app() -> Fragment:
    rows = signal([("a", "first"), ("b", "second")])
    held["rows"] = cast("Signal[object]", rows)

    return native.Div(each(rows, row, key=lambda value: value[0]))


def repeated_app() -> Fragment:
    values = [signal(index) for index in range(3)]

    return native.P(str(len(values)))


class CallableApp:
    def __call__(self) -> Fragment:
        count = signal(0)
        draft = local_state("draft", "")
        held["count"] = cast("Signal[object]", count)
        held["draft"] = cast("Signal[object]", draft)

        return native.Div(native.P(count), native.Input(bind_value=draft))
